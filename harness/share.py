"""Team sharing — export/import a bundle of templates + the design profile.

One JSON file carries all your saved templates and your house-style profile, so a
teammate (or another machine) can import them and get the same starters + taste.
Built on top of the templates + profile stores; stdlib only.
"""

from __future__ import annotations

from harness import profile as profile_mod
from harness import templates

BUNDLE_KIND = "agent1-harness-bundle"
BUNDLE_VERSION = 1


def export_bundle(templates_dir: str, profile: dict | None = None) -> dict:
    """Collect all user templates + the design profile into a shareable bundle."""
    tpls = []
    for meta in templates.list_templates(templates_dir, include_builtins=False):
        t = templates.load(templates_dir, meta["id"])
        if t:
            tpls.append(t)
    return {"kind": BUNDLE_KIND, "version": BUNDLE_VERSION,
            "profile": profile or {}, "templates": tpls}


def import_bundle(templates_dir: str, profile_path: str, bundle: dict, *,
                  apply_profile: bool = True) -> dict:
    """Save a bundle's templates (skipping invalid ones) and optionally adopt its
    profile. Returns counts. Accepts a single template too (graceful)."""
    if not isinstance(bundle, dict):
        return {"error": "not a valid bundle"}
    items = bundle.get("templates")
    if items is None and bundle.get("files") is not None:
        items = [bundle]  # someone handed us a single template
    saved, skipped = 0, 0
    for t in items or []:
        try:
            templates.save(templates_dir, t)
            saved += 1
        except (ValueError, TypeError):
            skipped += 1
    applied = False
    if apply_profile and bundle.get("profile"):
        profile_mod.save(profile_path, bundle["profile"])
        applied = True
    return {"imported": saved, "skipped": skipped, "profile_applied": applied}
