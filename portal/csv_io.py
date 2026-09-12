"""CSV import and export for the staff directory and staff groups.

Import is all-or-nothing: every row is validated first and nothing is
written if any row has a problem. Staff are matched by badge ID (rows for
unknown badges create staff, known badges are updated), and an import
never deletes anything. Exports open cleanly in Excel: UTF-8 with a BOM,
phones formatted as (312) 555-0147, and values Excel would treat as
formulas are escaped.
"""

import csv
import io
import re
from collections import OrderedDict

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Role, Staff, StaffGroup
from .phones import format_us_phone, normalize_phone

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
STAFF_HEADERS = ["Name", "Badge ID", "Mobile Phone", "Role", "Active", "Groups"]
GROUP_HEADERS = ["Group", "Badge ID", "Name", "Role"]

# Header matching is loose: lower-cased with punctuation/spaces removed,
# then looked up here, so "Mobile Phone", "mobile_phone" and "Cell" all work.
_STAFF_ALIASES = {
    "name": {"name", "fullname", "staffname", "employeename", "employee"},
    "badge_id": {"badge", "badgeid", "badgenumber", "badgeno", "employeeid", "staffid", "id"},
    "mobile_phone": {
        "phone", "mobile", "mobilephone", "cell", "cellphone", "phonenumber",
        "mobilenumber", "cellnumber", "telephone",
    },
    "role": {"role", "title", "position", "jobtitle"},
    "is_active": {"active", "status", "isactive"},
    "groups": {"groups", "group", "teams", "team", "groupmemberships", "memberships"},
}
_GROUP_ALIASES = {
    "group": {"group", "groupname", "team", "teamname", "groups"},
    "badge_id": _STAFF_ALIASES["badge_id"],
}
_REQUIRED_STAFF = ["name", "badge_id", "mobile_phone", "role"]
_LABELS = {"name": "Name", "badge_id": "Badge ID", "mobile_phone": "Mobile Phone", "role": "Role"}
_TRUE = {"", "yes", "y", "true", "t", "1", "active", "x"}
_FALSE = {"no", "n", "false", "f", "0", "inactive"}
_FORMULA_PREFIXES = ("=", "+", "-", "@")


# --- Reading uploads ------------------------------------------------------------

def decode_upload(uploaded):
    """Text of an uploaded CSV: UTF-8, or Windows-1252 as Excel often saves."""
    if uploaded.size > MAX_UPLOAD_BYTES:
        raise ValidationError("That file is larger than 2 MB.")
    raw = uploaded.read()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError("Could not read that file as text. Save it as CSV (UTF-8) and try again.")


def _guard(value):
    """Escape values that Excel would otherwise interpret as formulas."""
    value = "" if value is None else str(value)
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def _clean(value):
    value = (value or "").strip()
    return value[1:] if value.startswith("'") else value  # undo _guard


def _reader(text):
    first_line = text.splitlines()[0] if text.strip() else ""
    delimiter = max(",;\t", key=first_line.count) if first_line else ","
    return csv.DictReader(io.StringIO(text), delimiter=delimiter)


def _norm(header):
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def _map_headers(fieldnames, aliases):
    """{field: actual header text} for every recognised column."""
    mapping = {}
    for actual in fieldnames or []:
        key = _norm(actual)
        for field, names in aliases.items():
            if key in names and field not in mapping:
                mapping[field] = actual
                break
    return mapping


def _found(fieldnames):
    return ", ".join(h for h in (fieldnames or []) if h) or "none"


# --- Staff ------------------------------------------------------------------------

def parse_staff_csv(text):
    """Returns (rows, errors). Rows are dicts ready for apply_staff_rows();
    errors are human-readable and numbered by spreadsheet row."""
    reader = _reader(text)
    headers = _map_headers(reader.fieldnames, _STAFF_ALIASES)
    missing = [_LABELS[f] for f in _REQUIRED_STAFF if f not in headers]
    if missing:
        return [], [
            f"Missing required column(s): {', '.join(missing)}. "
            f"Columns found: {_found(reader.fieldnames)}."
        ]

    rows, errors, seen = [], [], {}
    for number, raw in enumerate(reader, start=2):
        def get(field):
            return _clean(raw.get(headers[field])) if field in headers else ""

        name, badge, phone_raw, role_raw = get("name"), get("badge_id"), get("mobile_phone"), get("role")
        if not any((name, badge, phone_raw, role_raw)):
            continue  # blank line

        if not name:
            errors.append(f"Row {number}: Name is blank.")
        elif len(name) > 100:
            errors.append(f"Row {number}: Name is longer than 100 characters.")
        if not badge:
            errors.append(f"Row {number}: Badge ID is blank.")
        elif len(badge) > 32:
            errors.append(f"Row {number}: Badge ID is longer than 32 characters.")
        elif badge in seen:
            errors.append(f"Row {number}: Badge ID {badge} also appears on row {seen[badge]}.")
        else:
            seen[badge] = number

        phone = phone_raw
        try:
            phone = normalize_phone(phone_raw)
        except ValidationError as exc:
            errors.append(f"Row {number}: {exc.messages[0]} (got “{phone_raw}”)")

        role = role_raw.upper()
        if role not in Role.values:
            errors.append(f"Row {number}: Role must be RN, LPN or CNA (got “{role_raw}”).")

        active_raw = get("is_active").lower()
        if active_raw in _TRUE:
            is_active = True
        elif active_raw in _FALSE:
            is_active = False
        else:
            is_active = True
            errors.append(f"Row {number}: Active must be yes or no (got “{get('is_active')}”).")

        groups = None
        if "groups" in headers:
            groups = [g.strip() for g in re.split(r"[;|]", get("groups")) if g.strip()]

        rows.append({
            "name": name, "badge_id": badge, "mobile_phone": phone, "role": role,
            "is_active": is_active, "groups": groups,
        })

    if not rows and not errors:
        errors.append("The file has a header row but no staff rows.")
    return rows, errors


def preview_staff_rows(rows):
    badges = [r["badge_id"] for r in rows]
    existing = set(Staff.objects.filter(badge_id__in=badges).values_list("badge_id", flat=True))
    known_groups = {name.lower() for name in StaffGroup.objects.values_list("name", flat=True)}
    wanted = {}
    for row in rows:
        for group in row["groups"] or []:
            wanted.setdefault(group.lower(), group)
    new_groups = sorted((n for k, n in wanted.items() if k not in known_groups), key=str.lower)
    return {
        "total": len(rows),
        "new": sum(1 for b in badges if b not in existing),
        "updated": sum(1 for b in badges if b in existing),
        "new_groups": new_groups,
        "has_groups": any(r["groups"] is not None for r in rows),
        "sample": rows[:8],
    }


def apply_staff_rows(rows):
    """Create/update staff (matched by badge ID) and, when the file had a
    Groups column, replace those staff members' group memberships."""
    with transaction.atomic():
        existing = {
            s.badge_id: s
            for s in Staff.objects.filter(badge_id__in=[r["badge_id"] for r in rows])
        }
        groups_by_lower = {g.name.lower(): g for g in StaffGroup.objects.all()}
        created = updated = groups_created = 0
        for row in rows:
            person = existing.get(row["badge_id"])
            if person is None:
                person = Staff.objects.create(
                    name=row["name"], badge_id=row["badge_id"],
                    mobile_phone=row["mobile_phone"], role=row["role"],
                    is_active=row["is_active"],
                )
                created += 1
            else:
                person.name = row["name"]
                person.mobile_phone = row["mobile_phone"]
                person.role = row["role"]
                person.is_active = row["is_active"]
                person.save()
                updated += 1
            if row["groups"] is not None:
                members = []
                for name in row["groups"]:
                    group = groups_by_lower.get(name.lower())
                    if group is None:
                        group = StaffGroup.objects.create(name=name)
                        groups_by_lower[name.lower()] = group
                        groups_created += 1
                    members.append(group)
                person.groups.set(members)
    return {"created": created, "updated": updated, "groups_created": groups_created}


def export_staff_csv():
    out = io.StringIO()
    out.write("﻿")  # BOM so Excel reads UTF-8 (accented names) correctly
    writer = csv.writer(out)
    writer.writerow(STAFF_HEADERS)
    for person in Staff.objects.order_by("name").prefetch_related("groups"):
        writer.writerow([
            _guard(person.name),
            _guard(person.badge_id),
            format_us_phone(person.mobile_phone),
            person.role,
            "yes" if person.is_active else "no",
            "; ".join(g.name for g in person.groups.all()),
        ])
    return out.getvalue()


# --- Groups ---------------------------------------------------------------------------

def parse_groups_csv(text):
    """Returns (mapping, errors) where mapping is {group name: [badge IDs]}
    in file order."""
    reader = _reader(text)
    headers = _map_headers(reader.fieldnames, _GROUP_ALIASES)
    if "group" not in headers:
        return OrderedDict(), [
            f"Missing required column: Group. Columns found: {_found(reader.fieldnames)}."
        ]

    mapping, errors, canonical = OrderedDict(), [], {}
    needed = set()
    for number, raw in enumerate(reader, start=2):
        group = _clean(raw.get(headers["group"]))
        badge = _clean(raw.get(headers["badge_id"])) if "badge_id" in headers else ""
        if not group and not badge:
            continue
        if not group:
            errors.append(f"Row {number}: Group is blank.")
            continue
        if len(group) > 100:
            errors.append(f"Row {number}: Group name is longer than 100 characters.")
            continue
        name = canonical.setdefault(group.lower(), group)
        members = mapping.setdefault(name, [])
        if badge and badge not in members:
            members.append(badge)
            needed.add(badge)

    known = set(Staff.objects.filter(badge_id__in=needed).values_list("badge_id", flat=True))
    unknown = sorted(needed - known)
    if unknown:
        errors.append(
            "No staff member has badge ID " + ", ".join(unknown)
            + " — add or import them under Staff first."
        )
    if not mapping and not errors:
        errors.append("The file has a header row but no group rows.")
    return mapping, errors


def preview_group_rows(mapping):
    known = {name.lower() for name in StaffGroup.objects.values_list("name", flat=True)}
    return {
        "total": len(mapping),
        "new": sum(1 for name in mapping if name.lower() not in known),
        "memberships": sum(len(v) for v in mapping.values()),
        "sample": [(name, len(badges)) for name, badges in list(mapping.items())[:8]],
    }


def apply_group_rows(mapping):
    """Create groups that are new and replace the members of every group
    listed in the file. Groups not in the file are untouched."""
    with transaction.atomic():
        by_lower = {g.name.lower(): g for g in StaffGroup.objects.all()}
        created = updated = memberships = 0
        for name, badges in mapping.items():
            group = by_lower.get(name.lower())
            if group is None:
                group = StaffGroup.objects.create(name=name)
                by_lower[name.lower()] = group
                created += 1
            else:
                updated += 1
            members = list(Staff.objects.filter(badge_id__in=badges))
            group.members.set(members)
            memberships += len(members)
    return {"created": created, "updated": updated, "memberships": memberships}


def export_groups_csv():
    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out)
    writer.writerow(GROUP_HEADERS)
    for group in StaffGroup.objects.order_by("name").prefetch_related("members"):
        members = list(group.members.all())
        if not members:
            writer.writerow([_guard(group.name), "", "", ""])
        for person in sorted(members, key=lambda p: p.name.lower()):
            writer.writerow([_guard(group.name), _guard(person.badge_id), _guard(person.name), person.role])
    return out.getvalue()
