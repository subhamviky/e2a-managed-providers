"""Model lifecycle check: is a profile's configured model about to retire, or already retired?

A retired model id is not caught by a build or an offline test: it surfaces only as an HTTP
404 at run time (the google_vertex default, gemini-2.5-pro, was retired on Agent Platform on
20 Oct 2026 before anyone noticed). This check reads the published dates in
model_lifecycle.yaml and is run by the release pipeline (static stage) and by
`python -m e2a_managed lifecycle <profile>`:

  * a configured id past its date            -> RETIRED (the release fails)
  * within `model_retirement_warn_days`      -> WARNING (the release goes on, the report says so)
  * an id the file does not list             -> not judged (the file lists dates it could verify)
"""
import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from e2a import resolve_config

DATA = Path(__file__).with_name("model_lifecycle.yaml")
MODEL_KEYS = ("model_id", "fallback_model_id", "delegated_runtime_id", "embedding_model_id")

# where each llm_service's models are served, for the lifecycle table
PLATFORM_OF = {"bedrock": "bedrock", "vertex_ai": "agent_platform", "azure_ai_foundry": "foundry",
               "ogx": "bedrock"}      # ogx_aws: its distribution's inference provider is Bedrock


def _table() -> Dict[str, Any]:
    return yaml.safe_load(DATA.read_text(encoding="utf-8"))


def _bare(model_id: str) -> str:
    """A Bedrock inference-profile id (us. / eu. / apac. / global.) names the same model."""
    head, _, rest = model_id.partition(".")
    return rest if head in ("us", "eu", "apac", "global") and rest else model_id


def check_models(platform: Optional[str], models: Dict[str, str], today: Optional[dt.date] = None,
                 warn_days: int = 90) -> List[Dict[str, Any]]:
    """One finding per configured model id the table lists: status RETIRED, WARNING or OK."""
    table = _table()
    entry = (table.get("platforms") or {}).get(platform or "") or {}
    known = entry.get("models") or {}
    today = today or dt.date.today()
    out = []
    for key, model_id in models.items():
        if not model_id or _bare(str(model_id)) not in known:
            continue
        info = known[_bare(str(model_id))]
        retires = info["retires"] if isinstance(info["retires"], dt.date) else dt.date.fromisoformat(str(info["retires"]))
        days = (retires - today).days
        status = "RETIRED" if days <= 0 else "WARNING" if days <= warn_days else "OK"
        out.append({"key": key, "model": model_id, "platform": platform, "retires": retires.isoformat(),
                    "days_left": days, "status": status, "replacement": info.get("replacement"),
                    "note": info.get("note"), "source": entry.get("source"), "checked": str(table.get("checked"))})
    return out


def check_profile(profile: str, config: Optional[Dict[str, Any]] = None, today: Optional[dt.date] = None):
    """The lifecycle findings for a profile's configured models (environment and overrides applied)."""
    from .bindings import bindings_from_profile
    from .offline import _import_cls
    b = bindings_from_profile(profile, overrides=config)
    cfg = {**b.profile.config, **(config or {})}
    platform = PLATFORM_OF.get(getattr(_import_cls(b.profile.bindings["llm_service"]), "platform", ""))
    models = {k: resolve_config(k, cfg) for k in MODEL_KEYS}
    warn_days = int(resolve_config("model_retirement_warn_days", cfg))
    return check_models(platform, {k: v for k, v in models.items() if isinstance(v, str)}, today, warn_days)


class ModelRetired(ValueError):
    """A configured model id is past its published retirement date."""
