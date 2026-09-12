"""
Память по конкурентам: накопленный профиль на каждый обменник в competitors.json.

Зачем: про одного обменника материалы приходят кусками — скриншот лендинга,
вордовский файл «О сервисе», отзывы с BestChange, разбор по ссылке. Раньше каждый
новый анализ затирал предыдущий, и в сравнении оставался только последний.
Теперь данные складываются в один профиль.

Как ищем «того же самого» обменника: имя приводится к общему виду через match_key
(кириллица в латиницу, всё лишнее выброшено), поэтому «GrumBot», «Grumbot»,
«grumbot.net» и «БухтаОбмена» / «buhtaobmena.me» попадают в один профиль.
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from backend.config import settings
from backend.utils import (
    apply_trust_cap,
    cap_reason,
    clean_name,
    competitor_name_from_url,
    domain_from_url,
    match_key,
)

logger = logging.getLogger(__name__)

# Флаги из отзывов приходят с пометкой источника. Для сравнения её снимаем,
# чтобы «не выдают деньги» и «отзывы: не выдают деньги» считались одним флагом.
REVIEW_PREFIX = re.compile(r"^\s*отзывы\s*:\s*", re.IGNORECASE)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _flag_key(text: str) -> str:
    """Ключ для сравнения флагов: без пометки источника, регистра и пунктуации."""
    cleaned = REVIEW_PREFIX.sub("", text or "")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned.lower())
    return " ".join(cleaned.split())


def _average(values: List[float]) -> Optional[float]:
    numbers = [v for v in values if v is not None]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 1)


class MemoryService:
    def __init__(self):
        self.file_path = Path(settings.memory_file)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self._save({})

    # --- чтение/запись файла ---

    def _load(self) -> Dict[str, dict]:
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, profiles: Dict[str, dict]) -> None:
        self.file_path.write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        self._export_results(profiles)

    def _export_results(self, profiles: Dict[str, dict]) -> None:
        """
        Читаемая копия результатов: без служебных ключей флагов.
        Обновляется вместе с competitors.json после каждого анализа.
        """
        payload = {
            "updated_at": _now(),
            "total": len(profiles),
            "competitors": [self._shape(p) for p in profiles.values()],
        }
        Path(settings.results_file).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _resolve_key(profiles: Dict[str, dict], name_key: str, hints: List[str]) -> str:
        """
        Ищем уже существующий профиль: сначала точный ключ, потом домен из ссылки,
        потом совпадение «бухтаобмена» / «buhtaobmena» по общему корню.
        Чтобы два скрина одного сайта не разъехались из-за разного написания имени.
        """
        candidates = []
        for raw in [name_key, *hints]:
            cleaned = match_key(clean_name(raw))
            if cleaned:
                candidates.append(cleaned)
            domain = domain_from_url(raw)
            if domain:
                slug = match_key(domain.split(".")[0])
                if slug:
                    candidates.append(slug)

        seen = []
        for item in candidates:
            if item not in seen:
                seen.append(item)

        for item in seen:
            if item in profiles:
                return item

        for profile in profiles.values():
            known = [profile.get("key") or ""]
            known += [match_key(clean_name(alias)) for alias in profile.get("aliases", [])]
            known += [match_key(domain.split(".")[0]) for domain in profile.get("domains", [])]
            known = [k for k in known if k]
            for item in seen:
                if item in known:
                    return profile["key"]
                # общий корень: buhtaobmena и buhtaobmename — это одно
                if len(item) >= 6 and any(
                    item.startswith(k) or k.startswith(item) for k in known if len(k) >= 6
                ):
                    return profile["key"]

        return name_key

    @staticmethod
    def source_id(content: bytes) -> str:
        """Отпечаток файла по содержимому: один и тот же файл не посчитается дважды."""
        return hashlib.sha256(content).hexdigest()[:16]

    # --- накопление ---

    def remember(
        self,
        competitor_name: str,
        kind: str,
        label: str,
        summary: str = "",
        source_id: Optional[str] = None,
        source_url: Optional[str] = None,
        name_is_reliable: bool = True,
        trust_score: Optional[int] = None,
        design_score: Optional[int] = None,
        red_flags: Optional[List[str]] = None,
        red_flag_categories: Optional[List[str]] = None,
        green_flags: Optional[List[str]] = None,
        payout_methods: Optional[List[str]] = None,
        exchanger_rate: Optional[float] = None,
        rate_source: Optional[str] = None,
        identity_hints: Optional[List[str]] = None,
    ) -> dict:
        """
        Добавляет один разбор (файл, ссылку, заметку) в профиль конкурента.
        Повторная загрузка того же файла обновляет запись, а не плодит копии.
        """
        competitor_name = clean_name(competitor_name)
        key = match_key(competitor_name)
        if not key:
            competitor_name = competitor_name_from_url(source_url or "") or "Без имени"
            key = match_key(competitor_name) or "bez-imeni"

        profiles = self._load()
        hints = [competitor_name, source_url or ""] + list(identity_hints or [])
        key = self._resolve_key(profiles, key, hints) or key
        profile = profiles.get(key) or {
            "key": key,
            "display_name": competitor_name,
            "name_is_reliable": name_is_reliable,
            "aliases": [],
            "domains": [],
            "first_seen": _now(),
            "sources": [],
            "red_flags": {},
            "green_flags": {},
            "payout_methods": {},
            "rates": [],
        }

        # имя из модели или домена важнее имени, собранного из названия файла
        if name_is_reliable and not profile.get("name_is_reliable"):
            profile["display_name"] = competitor_name
            profile["name_is_reliable"] = True

        if competitor_name and competitor_name not in profile["aliases"]:
            profile["aliases"].append(competitor_name)

        domain = domain_from_url(source_url or "")
        if domain and domain not in profile["domains"]:
            profile["domains"].append(domain)

        entry = {
            "id": source_id or hashlib.sha256(f"{kind}|{label}|{_now()}".encode()).hexdigest()[:16],
            "kind": kind,
            "label": label,
            "url": source_url,
            "at": _now(),
            "summary": (summary or "")[:400],
            "trust_score": trust_score,
            "design_score": design_score,
        }
        # тот же файл загрузили повторно — заменяем запись, а не добавляем вторую
        profile["sources"] = [s for s in profile["sources"] if s["id"] != entry["id"]]
        profile["sources"].append(entry)

        self._merge_flags(profile["red_flags"], red_flags, entry, red_flag_categories)
        self._merge_flags(profile["green_flags"], green_flags, entry)
        self._merge_flags(profile["payout_methods"], payout_methods, entry)

        if exchanger_rate is not None:
            profile["rates"].append({
                "rate": exchanger_rate,
                "source": rate_source or entry["label"],
                "at": entry["at"],
            })
            profile["rates"] = profile["rates"][-settings.max_score_points:]

        profile["last_seen"] = entry["at"]
        profiles[key] = profile
        self._save(profiles)
        return profile

    @staticmethod
    def _merge_flags(
        store: Dict[str, dict],
        values: Optional[List[str]],
        entry: dict,
        categories: Optional[List[str]] = None,
    ) -> None:
        """
        Складывает флаги без повторов, запоминая когда и откуда каждый пришёл.
        Категория (тяжесть) едет вместе с флагом: она нужна, чтобы потолок оценки
        работал и на уровне профиля, а не только внутри одного разбора.
        """
        cats = list(categories or [])
        for index, raw in enumerate(values or []):
            text = (raw or "").strip()
            if not text:
                continue
            key = _flag_key(text)
            if not key:
                continue
            category = cats[index] if index < len(cats) else None

            existing = store.get(key)
            if existing:
                existing["count"] += 1
                existing["last_seen"] = entry["at"]
                if entry["label"] not in existing["sources"]:
                    existing["sources"].append(entry["label"])
                # формулировка из отзывов информативнее — оставляем её
                if REVIEW_PREFIX.match(text) and not REVIEW_PREFIX.match(existing["text"]):
                    existing["text"] = text
                # категорию не затираем пустой: один источник мог её не проставить
                if category and not existing.get("category"):
                    existing["category"] = category
            else:
                store[key] = {
                    "text": text,
                    "count": 1,
                    "first_seen": entry["at"],
                    "last_seen": entry["at"],
                    "sources": [entry["label"]],
                    "category": category,
                }

        limit = settings.max_flags_per_profile
        if len(store) > limit:
            keep = sorted(
                store.items(),
                key=lambda item: (-item[1].get("count", 1), item[1].get("first_seen", "")),
            )[:limit]
            store.clear()
            store.update(keep)

    # --- чтение профилей ---

    def get_profiles(self) -> List[dict]:
        """Профили в удобном для интерфейса виде: со сведёнными оценками."""
        return [self._shape(p) for p in self._load().values()]

    def get_profile(self, competitor_name: str) -> Optional[dict]:
        profile = self._load().get(match_key(clean_name(competitor_name)))
        return self._shape(profile) if profile else None

    @staticmethod
    def _shape(profile: dict) -> dict:
        sources = profile.get("sources", [])
        trust_values = [s.get("trust_score") for s in sources if s.get("trust_score") is not None]
        design_values = [s.get("design_score") for s in sources if s.get("design_score") is not None]

        def flags(store: Dict[str, dict]) -> List[dict]:
            items = list(store.values())
            # то, что подтвердилось несколькими источниками, показываем выше
            items.sort(key=lambda f: (-f.get("count", 1), f.get("first_seen", "")))
            return items

        rates = profile.get("rates", [])
        red_store = profile.get("red_flags", {})

        # Потолок по тяжёлым флагам должен работать и здесь, а не только внутри
        # одного разбора: иначе чистый скриншот (8) и жалоба на блокировку карты (3)
        # усреднятся в 5.5, и тяжёлый флаг растворится. Если хоть один источник
        # нашёл блокировку карты — профиль выше потолка не поднимается.
        red_categories = [f.get("category") for f in red_store.values() if f.get("category")]
        profile_trust = apply_trust_cap(_average(trust_values), red_categories)

        return {
            "key": profile.get("key"),
            "competitor_name": profile.get("display_name") or "Без имени",
            "aliases": profile.get("aliases", []),
            "domains": profile.get("domains", []),
            "first_seen": profile.get("first_seen"),
            "last_seen": profile.get("last_seen"),
            "sources": sorted(sources, key=lambda s: s.get("at", "")),
            "sources_count": len(sources),
            "trust_score": profile_trust,
            "trust_reason": cap_reason(red_categories),
            "trust_observations": len(trust_values),
            "design_score": _average(design_values),
            "design_observations": len(design_values),
            "red_flags": flags(profile.get("red_flags", {})),
            "green_flags": flags(profile.get("green_flags", {})),
            "payout_methods": flags(profile.get("payout_methods", {})),
            "exchanger_rate": rates[-1]["rate"] if rates else None,
            "rate_source": rates[-1]["source"] if rates else None,
            "rate_fetched_at": rates[-1].get("at") if rates else None,
        }

    # --- правки руками ---

    def merge(self, source_name: str, target_name: str) -> bool:
        """
        Склеить два профиля, если автоматика не поняла, что это один обменник
        (например, «Обменка24» в файле и «obmenka-24.com» в ссылке).
        """
        profiles = self._load()
        source_key = match_key(clean_name(source_name))
        target_key = match_key(clean_name(target_name))
        if source_key == target_key:
            # старые записи могли лежать под сырыми ключами вроде buhtaobmename
            if source_name in profiles and target_name in profiles and source_name != target_name:
                source_key, target_key = source_name, target_name
            else:
                return False
        if source_key not in profiles or target_key not in profiles:
            return False

        source = profiles.pop(source_key)
        target = profiles[target_key]

        for alias in source.get("aliases", []):
            if alias not in target["aliases"]:
                target["aliases"].append(alias)
        for domain in source.get("domains", []):
            if domain not in target["domains"]:
                target["domains"].append(domain)

        known = {s["id"] for s in target["sources"]}
        for entry in source.get("sources", []):
            if entry["id"] not in known:
                target["sources"].append(entry)
                MemoryService._merge_flags(target["red_flags"], [], entry)

        for field in ("red_flags", "green_flags", "payout_methods"):
            for key, flag in source.get(field, {}).items():
                existing = target[field].get(key)
                if existing:
                    existing["count"] += flag.get("count", 1)
                    existing["sources"] = list(dict.fromkeys(existing["sources"] + flag.get("sources", [])))
                    existing["last_seen"] = max(existing["last_seen"], flag.get("last_seen", ""))
                else:
                    target[field][key] = flag

        target["rates"] = (target.get("rates", []) + source.get("rates", []))[-20:]
        target["first_seen"] = min(target.get("first_seen", ""), source.get("first_seen", "")) or _now()
        target["last_seen"] = max(target.get("last_seen", ""), source.get("last_seen", "")) or _now()

        self._save(profiles)
        return True

    def rename(self, competitor_name: str, new_name: str) -> bool:
        profiles = self._load()
        key = match_key(clean_name(competitor_name))
        if key not in profiles or not new_name.strip():
            return False
        profiles[key]["display_name"] = new_name.strip()
        profiles[key]["name_is_reliable"] = True
        self._save(profiles)
        return True

    def forget(self, competitor_name: str) -> bool:
        profiles = self._load()
        if profiles.pop(match_key(clean_name(competitor_name)), None) is None:
            return False
        self._save(profiles)
        return True

    def clear(self) -> None:
        self._save({})


memory_service = MemoryService()
