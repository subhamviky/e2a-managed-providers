"""python -m e2a_managed <command>

    profiles                 list this package's managed-provider profiles
    verify <profile>         bind every seam and check the profile's required features
    env <profile>            the environment variables a deployment of <profile> sets (.env template)
    reference                configuration reference for every managed key (markdown)
    matrix                   capability matrix of all profiles (markdown, generated from the bindings)
"""
import re
import sys

from e2a import E2A_CONFIG

from . import available_profiles, bindings_from_profile
from .config_keys import MANAGED_KEYS
from .profile import ProviderProfile, provider_profiles

_VAR = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


def _env(profile: str) -> str:
    p = ProviderProfile.load(profile)
    raw = re.sub(r"(?m)#.*$", "", open(p.source, encoding="utf-8").read())   # placeholders in comments are examples
    lines = [f"# {p.name}: {p.provider} on {p.cloud}", f"E2A_PROVIDER_PROFILE={p.name}"]
    seen = set()
    for name, default in _VAR.findall(raw):
        if name in seen:
            continue
        seen.add(name)
        secret = any(k.env == name and k.secret for k in E2A_CONFIG.values())
        lines.append(f"{name}=" + ("" if secret or default == "" else default) + ("   # secret: inject at runtime" if secret else ""))
    return "\n".join(lines)


def _reference() -> str:
    out, group = [], None
    for k in MANAGED_KEYS:
        if k.group != group:
            group = k.group
            out += ["", f"### {group}", "", "| Key | Environment variable | Default | Used by |", "|---|---|---|---|"]
        out.append(f"| {k.key} | {k.env} | {'(secret)' if k.secret else repr(k.default)} | {k.used_by} |")
    return "\n".join(out).strip()


def _matrix() -> str:
    from .capabilities import Capability
    names = provider_profiles()
    provided = {n: {c for caps in bindings_from_profile(n).verify([]).values() for c in caps} for n in names}
    rows = ["| Capability | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
    for cap in Capability:
        rows.append(f"| {cap.value} | " + " | ".join("yes" if cap.value in provided[n] else "-" for n in names) + " |")
    return "\n".join(rows)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "profiles":
        for name in provider_profiles():
            p = ProviderProfile.load(name)
            print(f"{name:32} {p.provider:10} {p.cloud:6} {len(p.bindings)} seams")
    elif cmd == "verify":
        provided = bindings_from_profile(args[1]).verify()
        for seam, caps in provided.items():
            print(f"{seam:22} {', '.join(caps)}")
        print(f"OK: {args[1]} provides every required feature")
    elif cmd == "env":
        print(_env(args[1]))
    elif cmd == "matrix":
        print(_matrix())
    elif cmd == "reference":
        print(_reference())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
