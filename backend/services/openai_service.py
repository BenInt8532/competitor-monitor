"""
Сервис работы с GPT-4o через ProxyAPI (OpenAI-совместимый эндпоинт).

Промпты — черновик ассистента, собранный из критериев безопасности, которые
сформулировал Ben (см. otchet-PEm08-multimodalnoe-prilozhenie.md, раздел 1).
Ben тестирует и дорабатывает формулировки в Cursor по факту первых прогонов.
"""

import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from backend.config import settings
from backend.models.schemas import CompetitorAnalysis, ImageAnalysis, ReviewInsight
from backend.utils import (
    RED_FLAG_CATEGORIES,
    apply_trust_cap,
    cap_reason,
    competitor_name_from_url,
    merge_payout_methods,
)

logger = logging.getLogger(__name__)


# Общий блок критериев — вставляется в оба системных промпта, чтобы модель
# использовала ОДИН и тот же список флагов везде.
SAFETY_CRITERIA_BLOCK = """
Флаги — ТОЛЬКО про безопасность сделки USDT→RUB (выплата на счёт / наличными).
Маркетинг в флаги не клади: «24/7», «удобный интерфейс», «много обменов»,
«лучшие курсы», «работаем с 20xx» без цифр, наличие чата само по себе.

=== Зелёные (безопасная практика) ===
- Выплата ОДНИМ платежом на сумму заявки, без нарезки
- Выплата по реквизитам счёта / банковским переводом (не только СБП и не только карта)
- Наличные (встреча, офис, курьер): карта и счёт в сделке не участвуют — риска блокировки нет
- AML/проверка источника ДО отправки крипты: правила видны заранее, можно проверить адрес
  и отказаться. Само объявление «мы делаем AML» — это плюс прозрачности, НЕ красный флаг
- Курс и сумма фиксируются в заявке (Fixed), окно фиксации названо
- Резерв заметно больше суммы сделки; на BestChange много отзывов и возраст не «вчера»
- Зелёный AML-тег BestChange / предварительная проверка кошелька до оплаты
- Официальный домен, контакты, регламент сроков выплаты и возврата написаны до сделки
- Отвечают на финансовые претензии по существу, а не шаблоном

=== Красные (опасная практика) ===
- Дробление выплаты на 2+ мелких случайных суммы (2318 + 489 + 3285) — типичный обход банков
- Выплата ТОЛЬКО СБП / только на карту, без перевода на реквизиты счёта
- Жалобы: карту или счёт заблокировали ПОСЛЕ выплаты от этого обменника
- AML/KYC/«служба безопасности» ВКЛЮЧАЮТСЯ ПОСЛЕ того, как клиент уже отправил крипту
- Комиссия за «разморозку», «верификацию», возврат (часто 5–20%) после удержания средств
- Требуют доп. документы или «ещё одну транзакцию» после оплаты заявки
- Просят держать банк открытым / «телефон в руках» на время сделки
- Меняют кошелёк или реквизиты в ходе сделки; просят доплату на новый адрес
- Уводят из заявки в Telegram/WhatsApp и меняют условия в чате
- Курс заметно лучше рынка без объяснения — приманка
- Мало отзывов / обменник новый, при этом крупные суммы
- Красный AML-тег BestChange, факты «грязной» крипты клиентам, затяжные необоснованные кейсы
- Просят сид-фразу, ключ кошелька, коды из банка
- Финансовые претензии на BestChange без внятного ответа или с нарушением своего регламента

=== Как отличить AML-плюс от AML-минуса ===
- ДО отправки, в правилах, с возможностью проверить адрес и отменить → green
- После получения крипты, заморозка + документы + удержание % → red
- Фраза «отказ от KYC может привести к возврату с комиссией» после оплаты → red
- Не выдумывай риск («может привести к заморозке»), если на сайте просто написано,
  что проверка бывает. Без факта заморозки или удержания это не red_flag

=== Что НЕ является флагом ===
- Красивый дизайн, слоган, «надёжный сервис»
- Наличие SEPA/EUR само по себе (к безопасности рублёвой выплаты не относится)
- Голословные «мошенники!» без даты, суммы и описания сделки
- Сайты «вернём ваши деньги» / «разоблачение» — они мажут любой обменник.
  В флаги не брать. Доверяй BestChange (отзывы и claim), конкретным цитатам.
- Рассуждения о том, есть ли обменник на BestChange, в каких направлениях он
  представлен, какой у него там рейтинг, резерв, возраст или число отзывов.
  Этого в присланном тебе тексте нет, а приложение знает такие данные точно —
  оно само читает таблицы BestChange. Не пиши «обменника нет на BestChange» и
  «мало отзывов на площадке»: такие выводы ты сделать не можешь, и они уже
  оказывались ложными. Флаг про мало отзывов ставь ТОЛЬКО если это сказано
  в самом тексте, который тебе дали.
"""

RED_FLAG_CATEGORIES_HINT = """
Для КАЖДОГО красного флага поставь категорию в red_flag_categories — в том же
порядке, что и сами флаги (сколько флагов, столько категорий). Только из списка:

  card_block      — жалобы, что карту или счёт заблокировали ПОСЛЕ выплаты
  splitting       — выплату дробят на несколько мелких платежей
  after_payment   — требуют доплату, документы или ещё транзакцию ПОСЛЕ отправки крипты
  aml_hold        — заморозка под AML/KYC с комиссией за разморозку или возврат
  bank_access     — просят держать личный кабинет банка открытым, «телефон в руках»
  sbp_only        — выплата только через СБП, без перевода на реквизиты счёта
  delays          — задержки выплаты, нарушение своего же регламента
  too_good_rate   — курс заметно лучше рынка без объяснения
  thin_reputation — мало отзывов или маленький резерв при крупных суммах
  other           — всё остальное

Тяжесть у флагов разная, и это влияет на trust_score:
- card_block — самый тяжёлый: деньги уже потеряны или заморожены у реального клиента.
  Если он есть, trust_score не может быть выше 3, сколько бы плюсов ни было.
- splitting, after_payment, aml_hold, bank_access — тяжёлые: не выше 4.
- остальные — минус балл каждый, потолок не ставят.
Быстрая поддержка и удобный сайт тяжёлый флаг НЕ компенсируют.
"""

def apply_severity(analysis) -> None:
    """
    Опускает оценку модели до потолка, если среди флагов есть тяжёлый.
    Вверх никогда не поднимает — модель могла занизить по своим соображениям,
    это её право. Наша задача — не дать высокой оценке ужиться с блокировкой карты.
    Заодно чистит категории от выдуманных значений и выравнивает их длину по флагам.
    """
    raw = list(getattr(analysis, "red_flag_categories", []) or [])
    flags = list(getattr(analysis, "red_flags", []) or [])

    # модель иногда возвращает меньше категорий, чем флагов — добиваем «other»
    categories = [c if c in RED_FLAG_CATEGORIES else "other" for c in raw[:len(flags)]]
    categories += ["other"] * (len(flags) - len(categories))
    analysis.red_flag_categories = categories

    score_field = "trust_score" if hasattr(analysis, "trust_score") else "reviews_trust_score"
    capped = apply_trust_cap(getattr(analysis, score_field, None), categories)
    if capped is not None:
        setattr(analysis, score_field, capped)

    if hasattr(analysis, "trust_reason"):
        analysis.trust_reason = cap_reason(categories)


PAYOUT_METHODS_HINT = """
payout_methods — полный список способов получить рубли, не один «главный».
Смотри калькулятор, список «получить», иконки банков, футер, блок направлений.
Бери всё, что реально есть: СБП, карта, Сбербанк, Т-Банк, Альфа-Банк, ВТБ,
банковский перевод, расчётный счёт, наличные (офис/встреча/курьер), QIWI, ЮMoney.
Один пункт = один способ, короткие имена. Банк, которого нет в тексте/на скрине, не выдумывай.
"""


class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.proxy_api_key,
            base_url=settings.proxy_api_base_url,
        )

    async def analyze_text(
        self,
        text: str,
        site_name: Optional[str] = None,
        site_url: Optional[str] = None,
    ) -> CompetitorAnalysis:
        """Анализ текста конкурента: описание, notes.md, вставленные отзывы"""
        site_line = self._site_line(site_name, site_url)
        system_prompt = f"""Ты — эксперт по конкурентному анализу крипто-обменников.
Проанализируй предоставленный текст (описание сервиса и/или отзывы клиентов)
и верни строго структурированный JSON-ответ.
{site_line}

{SAFETY_CRITERIA_BLOCK}
{RED_FLAG_CATEGORIES_HINT}
{PAYOUT_METHODS_HINT}

Формат ответа (строго JSON, без markdown-обёртки):
{{
  "strengths": ["сильная сторона 1", ...],
  "weaknesses": ["слабая сторона 1", ...],
  "unique_offers": ["уникальное предложение 1", ...],
  "recommendations": ["рекомендация 1", ...],
  "summary": "краткое резюме анализа",
  "detected_name": "название обменника или его домен, если он упомянут в тексте, иначе null",
  "exchanger_rate": "курс рублей за 1 USDT числом, если он прямо указан (например 85.32), иначе null",
  "payout_methods": ["полный список способов выплаты, каждый отдельным пунктом"],
  "trust_score": 7,
  "design_score": null,
  "red_flags": ["конкретный найденный опасный признак с кратким пояснением/цитатой"],
  "red_flag_categories": ["категория каждого красного флага из списка выше, в том же порядке"],
  "green_flags": ["конкретный найденный безопасный признак с кратким пояснением/цитатой"]
}}

Важно:
- если название или адрес сайта уже даны в тексте — используй их, не оставляй trust_score пустым
- design_score — только если в материалах есть описание скрина/лендинга, иначе null
- detected_name — только если название реально встречается в тексте, не угадывай
- exchanger_rate — только курс пары USDT→рубль. Курс биткоина или другой пары не подставляй,
  диапазоны и «от ...» не считаются. Не уверен — ставь null
- payout_methods — все способы из текста, не сокращай до одного
- strengths/weaknesses/unique_offers/recommendations — по 3-5 пунктов каждый
- trust_score от 0 (явный скам) до 10 (максимально безопасный по критериям выше)
- red_flags и green_flags могут быть пустыми списками, если по тексту ничего не найдено
- Пиши на русском языке, будь конкретен
"""

        response = await self.client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        data = json.loads(response.choices[0].message.content)
        # служебное поле приложения, модель его не заполняет
        data.pop("enrichment_note", None)
        analysis = CompetitorAnalysis(**data)
        analysis.payout_methods = merge_payout_methods(analysis.payout_methods)
        apply_severity(analysis)
        return analysis

    async def analyze_image(
        self,
        image_base64: str,
        mime_type: str = "image/jpeg",
        site_name: Optional[str] = None,
        site_url: Optional[str] = None,
    ) -> ImageAnalysis:
        """Анализ скриншота лендинга/калькулятора обменника"""
        site_line = self._site_line(site_name, site_url)
        system_prompt = f"""Ты — эксперт по визуальному маркетингу и безопасности крипто-обменников.
Проанализируй изображение (скриншот лендинга, калькулятора или формы заявки обменника)
и верни строго структурированный JSON-ответ.
{site_line}

{SAFETY_CRITERIA_BLOCK}
{RED_FLAG_CATEGORIES_HINT}
{PAYOUT_METHODS_HINT}

Формат ответа (строго JSON, без markdown-обёртки):
{{
  "description": "детальное описание того, что изображено",
  "detected_name": "название обменника с картинки: логотип, адрес сайта в шапке или в адресной строке браузера. Если не видно — null",
  "marketing_insights": ["инсайт 1", ...],
  "design_score": 7,
  "visual_style_analysis": "анализ визуального стиля",
  "safety_claims": ["маркетинговые фразы про безопасность, видимые на скрине, например 'выплата одним платежом', 'AML-проверка до перевода'"],
  "recommendations": ["рекомендация 1", ...],
  "trust_score": 7,
  "red_flags": ["опасный признак, который реально видно на скрине"],
  "red_flag_categories": ["категория каждого красного флага из списка выше, в том же порядке"],
  "green_flags": ["безопасный признак, который реально видно на скрине"],
  "payout_methods": ["все способы выплаты, которые видно на скрине, каждый отдельным пунктом"]
}}

Важно:
- название сайта, если оно уже известно (файл, таблица, профиль), используй в оценке. Не оставляй trust_score пустым
- detected_name читай прямо с изображения (логотип, домен, заголовок вкладки). Если известно имя сайта — сверься с ним. Не угадывай по стилю
- design_score от 0 до 10 — не «красиво», а доверие с первого взгляда (Nielsen / Stanford):
  опрятность, читаемые правила и курс, контакты, отзывы/цифры, не выглядит как любительский одностраничник.
  0–4 любительски/грязно, 5–7 обычно, 8–10 похоже на настоящий сервис
- visual_style_analysis: 1–2 предложения, что именно вселяет или отнимает доверие (контакты видны?
  правила/курс на первом экране? стоковые картинки? кривые шрифты?)
- marketing_insights и recommendations — по 3-5 пунктов
- safety_claims — только то, что реально видно текстом на изображении, не придумывай
- trust_score от 0 до 10 по чеклисту безопасности: что видно на скрине + известное имя/адрес сайта
- red_flags / green_flags — только факты с картинки, не слоганы вроде «24/7»
- payout_methods — все банки и способы из калькулятора/иконок, не один пункт
- Пиши на русском языке
"""

        user_text = "Проанализируй это изображение обменника."
        if site_name or site_url:
            user_text = (
                f"Это скриншот обменника «{site_name or 'неизвестно'}»"
                f"{', сайт ' + site_url if site_url else ''}. "
                "Имя и адрес уже известны — поставь и trust_score, и design_score."
            )

        response = await self.client.chat.completions.create(
            model=settings.openai_vision_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                        },
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        data = json.loads(response.choices[0].message.content)
        image = ImageAnalysis(**data)
        image.payout_methods = merge_payout_methods(image.payout_methods)
        apply_severity(image)
        return image

    async def analyze_reviews(self, reviews_text: str, domain: str) -> ReviewInsight:
        """
        Разбор отзывов, собранных с BestChange и сторонних площадок.
        Задача узкая: вытащить конкретику из чужого опыта, а не пересказать лендинг.
        """
        system_prompt = f"""Ты — аналитик, который читает отзывы клиентов о крипто-обменнике
({domain}) и вытаскивает из них факты о безопасности сделок.

Тебе дают сырой текст страниц с отзывами с нескольких площадок. Там же будет
лишнее: меню сайта, реклама, описание от самого обменника, формы отправки отзыва.
Всё это игнорируй — тебя интересуют только высказывания реальных клиентов о сделках.

{SAFETY_CRITERIA_BLOCK}
{RED_FLAG_CATEGORIES_HINT}

Формат ответа (строго JSON, без markdown-обёртки):
{{
  "summary": "краткий вывод: о чём вообще пишут клиенты",
  "red_flags": ["опасный признак из отзывов, с пояснением"],
  "red_flag_categories": ["категория каждого красного флага из списка выше, в том же порядке"],
  "green_flags": ["хороший признак из отзывов, с пояснением"],
  "complaint_quotes": ["дословная цитата жалобы, желательно с датой/суммой"],
  "positive_quotes": ["дословная цитата позитивного отзыва"],
  "reviews_trust_score": 7,
  "ignored_claims": ["обвинение, которое ты отбросил как голословное, и почему"]
}}

Правила:
- reviews_trust_score от 0 (клиентов явно кидают) до 10 (жалоб по существу нет)
- Цитаты бери ДОСЛОВНО из текста. Ничего не придумывай и не пересказывай своими словами
- Финансовая претензия весит больше обычного отзыва — это спор о реальных деньгах
- Если жалоба без конкретики (нет даты, суммы, описания сделки) — в ignored_claims, а не в red_flags
- Массовые одинаковые хвалебные отзывы в один день — это сам по себе red flag (накрутка)
- Любой список может быть пустым, если в тексте ничего подходящего нет
- Пиши на русском языке
"""

        response = await self.client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": reviews_text},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        data = json.loads(response.choices[0].message.content)
        # sources и reviews_found проставляет reviews_service — модель о них не знает
        data.pop("sources", None)
        data.pop("reviews_found", None)
        insight = ReviewInsight(**data)
        apply_severity(insight)
        return insight

    async def score_known_site(
        self,
        site_name: str,
        site_url: Optional[str],
        materials: str,
    ) -> CompetitorAnalysis:
        """
        Оценка по уже известному сайту и собранным материалам.
        Нужна старым профилям, где скрины есть, а trust_score модель не ставила.
        """
        header = (
            f"Обменник: {site_name}\n"
            f"Сайт: {site_url or 'не указан'}\n\n"
            "Название и адрес сайта уже известны. trust_score поставь обязательно. "
            "design_score — если по описаниям скрина можно судить о виде лендинга, иначе null.\n\n"
            "Уже собранные материалы:\n"
        )
        return await self.analyze_text(header + materials, site_name=site_name, site_url=site_url)

    async def analyze_website_screenshot(
        self,
        screenshot_base64: str,
        page_text: Optional[str],
        url: str,
    ) -> CompetitorAnalysis:
        """
        Комбинированный анализ сайта: скриншот + извлечённый текст страницы.
        Используется в связке с parser_service.py (Selenium).
        """
        site_name = competitor_name_from_url(url)
        combined_text = f"URL: {url}\n\nТекст страницы:\n{page_text or '(текст не извлечён)'}"
        text_result = await self.analyze_text(combined_text, site_name=site_name, site_url=url)
        try:
            image_result = await self.analyze_image(
                screenshot_base64, site_name=site_name, site_url=url
            )
            extra_green = image_result.green_flags or image_result.safety_claims
            text_result.red_flags = list(dict.fromkeys(text_result.red_flags + image_result.red_flags))
            text_result.green_flags = list(dict.fromkeys(text_result.green_flags + extra_green))
            text_result.payout_methods = merge_payout_methods(
                text_result.payout_methods, image_result.payout_methods
            )
            text_result.design_score = image_result.design_score
            if not text_result.detected_name:
                text_result.detected_name = image_result.detected_name or site_name
        except Exception:
            logger.warning("Разбор скриншота %s не удался, остаётся только текст", url, exc_info=True)
        return text_result

    @staticmethod
    def _site_line(site_name: Optional[str], site_url: Optional[str]) -> str:
        if not site_name and not site_url:
            return ""
        return (
            f"\nИзвестный обменник: {site_name or 'не указан'}"
            f"{', сайт ' + site_url if site_url else ''}. "
            "Используй это имя в оценке, не оставляй trust_score пустым.\n"
        )


openai_service = OpenAIService()
