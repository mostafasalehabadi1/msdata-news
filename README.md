# msdata-news

خبرنویس خودکار بورس کالای msdata.ir. هیچ ماشین ایرانی به گیت‌هاب push نمی‌کند.

1. `fetch-data` (GitHub Actions، ۱۳:۳۰ / ۱۵:۳۰ / ۱۸:۳۰ تهران، شنبه تا چهارشنبه): داده‌ی آخرین روز معاملاتی را از API های msdata در `data/` می‌گذارد.
2. سشن ابری Claude (زمان‌بندی‌شده، طبق `CLAUDE.md`): خبرها را در `news/` می‌نویسد و push می‌کند.
3. `validate-and-publish`: هر push روی `news/` با `tools/validate.py` چک می‌شود؛ فقط کامیت سالم به شاخه‌ی `published` می‌رود.
4. هاست msdata.ir شاخه‌ی `published` را می‌کشد و خبرها را منتشر می‌کند.
