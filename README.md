# Saudi AI Sniper

نظام قراءة وتنبيه للسوق السعودي. لا ينفّذ أوامر شراء أو بيع آليًا.

## Telegram العام والتحليل عند الطلب

- التنبيهات تصل للمالك عبر `TELEGRAM_CHAT_ID` وللقناة العامة عبر `TELEGRAM_CHANNEL_ID`.
- أضف البوت مشرفًا في القناة واضبط `TELEGRAM_CHANNEL_ID` باسم مثل `@SaudiSniperChannel` أو رقم القناة.
- شغّل نسخة واحدة فقط من `support_bot.py` عبر `Dockerfile.support`.
- أي مستخدم يستطيع مراسلة البوت باسم الشركة أو الرمز: `أرامكو`، `2222`، `الراجحي`، `stc`.
- الرد يوضح السعر، الدعوم والمقاومات، الزخم، الوضع المالي، أمر LIMIT مقترحًا تحت السعر، وقفًا افتراضيًا 2%، هدفين، ومدة الخطة.
- إذا كان الاتجاه ضعيفًا يرفض البوت إعطاء دخول فوري ويعرض مستوى المراقبة بدل اختلاق توصية.

## متغيرات التشغيل

- `SAHMK_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_CHANNEL_ID` (اختياري حتى إنشاء القناة)
- `RATE_LIMIT_PER_MIN` (الافتراضي 10)
- `SAUDI_STOP_LOSS_PCT` (الافتراضي 0.02)

## تشغيل خدمة الاستعلام

```bash
docker build -f Dockerfile.support -t saudi-support-bot .
docker run --env-file .env saudi-support-bot
```

لا تشغّل أكثر من Pod واحد لنفس التوكن؛ Telegram يعيد 409 عند وجود أكثر من polling instance.

## الاختبارات

```bash
python -m unittest -v test_smoke.py
python -m unittest discover -s tests -v
```

التحليل آلي لأغراض معلوماتية وليس توصية استثمارية.
