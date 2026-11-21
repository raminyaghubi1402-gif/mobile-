# وضعیت واقعی Forward Paper — 2026-11-22

## وضعیت فعلی (بر اساس main)

- Workflow `.github/workflows/paper-forward.yml` فقط `workflow_dispatch` دارد؛ schedule فعال نیست.
- Workflow با `contents: read` اجرا می‌شود و state را روی main ذخیره/commit نمی‌کند.
- `run_forward_cycle` در `src/lili_trader/paper_forward.py` در ابتدای کار `disabled_legacy_engine` برمی‌گرداند؛ قبل از fetch بازار، خواندن/نوشتن state یا ثبت تصمیم.
- تست `test_legacy_cycle_is_disabled_before_network_or_state_access` این رفتار را تأیید می‌کند.
- `data/paper/forward/account.json` و `decisions.jsonl` snapshot قدیمی هستند؛ آخرین چرخهٔ درج‌شده 2026-11-17 UTC. این فایل‌ها نشان‌دهندهٔ Paper جاری نیستند.

## چرا خاموش است؟

موتور قدیمی سه‌تایم‌فریمی در migration به موتورهای مستقل خاموش شده. موتور Trend Pullback در PR #12 فقط research signal می‌دهد و هنوز به Paper، position sizing، SL/TP یا execution متصل نیست. فعال‌کردن schedule در این وضعیت فقط چرخهٔ disabled را اجرا می‌کند و ممکن است گزارش ظاهراً «Paper» ولی بدون تحلیل تولید کند.

## شروط فعال‌سازی بعدی

1. موتور مستقل با historical/walk-forward diagnostics و anti-lookahead ممیزی شود.
2. freshness و gap guards برای data feed تثبیت شوند.
3. سیاست مشترک risk/position accounting و paper fills جداگانه تست شوند؛ ورود/خروج و هزینه‌ها باید مشخص باشند.
4. state persistence به branch جدا از `main` طراحی شود؛ هر چرخه باید قابل بازیابی و append-only باشد.
5. schedule با cadence و هزینهٔ GitHub Actions مشخص و پس از بازبینی/ادغام فعال شود.
6. گزارش‌ها به‌وضوح Paper-only و غیرقابل‌تعبیر به سفارش واقعی باشند.

تا این شروط برآورده نشده‌اند، workflow دستی باقی می‌ماند و موتور قدیمی غیرفعال است.
