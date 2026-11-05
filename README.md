# Saudi AI Sniper

نظام قراءة وتنبيه للسوق السعودي. لا ينفّذ أوامر شراء أو بيع آليًا.

## محرك المضاربة اللحظي

- المصدر المعتمد للأسعار والشموع والسيولة والعمق والتنفيذات هو **SAHMK Pro فقط**؛ اتصال IBKR مخصص للسوق الأمريكي ولا يُستخدم بديلًا لبيانات تداول السعودية.
- `saudi_intraday_engine.py` يقرأ جميع أسهم TASI النشطة، يرتبها بقيمة التداول، ثم يحلل أعلى 20 سهمًا فقط للمحافظة على حصة API.
- التحليل مبني على شموع 15 دقيقة المكتملة: EMA 9/20، RSI، MACD، ATR، Bollinger، VWAP، OBV، CCI، StochRSI، Williams %R وADX، مع دعم/مقاومة وعمق أفضل 5 مستويات واتجاه التنفيذات.
- لا يعتبر ارتفاع السعر إشارة. يلزم اختراق مغلق بحجم مؤكد أو ارتداد دعم مكتمل، مع سيولة وعمق وتنفيذات متوافقة، ويرفض البيانات المتأخرة والمطاردة.
- المحرك للقراءة فقط، بلا حلقة تشغيل أو Telegram أو أي دالة تنفيذ أوامر. `scan_once()` يعيد الفرص المشروطة ليستهلكها عامل منفصل لاحقًا.

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

## SAHMK Pro intelligence

The private Telegram analysis service now uses the approved SAHMK Pro access for:

- Real-time quote and timestamp, with delayed-data labelling.
- Top-5 market depth (/market/depth/{symbol}/) and bid/ask value imbalance.
- Up to 100 recent executions and buy/sell aggressor flow.
- Completed 60-minute candles for intraday trend and nearby levels.
- Extended quarterly ratios and financial statements.
- Dividends, TASI context, and important company events.
- Independent block failures: a 403 plan/entitlement response disables only that block.

Verified account capabilities from the read-only entitlement probes: Pro stock WebSocket connection (60 symbols per connection, 20 per subscription call) and approved top-5 depth snapshot. The bot intentionally requests no more than five depth levels.

Configuration:

- SAHMK_API_KEY — Northflank secret only.
- SAHMK_GLOBAL_RATE_PER_MIN — default 300, hard-capped at 450 (Pro account limit is 500/min).
- RATE_LIMIT_PER_MIN — private Telegram analysis requests per user, default 10.
- Cache TTLs: quote/depth/trades 5s, TASI 30s, intraday 1h, financials/ratios/dividends 24h.

This service supplies compact derived analysis. It does not place Saudi-market orders and does not redistribute a raw standalone market-data feed.


## التنبيهات العامة المختصرة

- تنبيه واحد لكل سهم وحالة، مع أقرب مستوى فعلي ووقت الرياض بدل التاريخ الخام.
- تُدمج مستويات الدعم المكسورة، ولا يعاد نفس النوع والمستوى خلال اليوم إلا بعد تغير الحالة.
- بيانات السعر يجب أن تكون حديثة وأن يصرح سهمك `is_delayed=false`.
- لمس الدعم أو تجاوز المقاومة ليس دخولًا مؤكدًا: يلزم إغلاق 15 دقيقة نهائي وحجم داعم؛ المراقب الحالي لا يثبت ذلك فلا يسميه فرصة مؤكدة ولا يختلق أهدافًا.
- يرسل للمالك وللقناة المعتمدة. قيمة `TELEGRAM_CHANNEL_ID` الفارغة في Actions تستخدم القناة المعتمدة بدل تعطيل مسارها.
- لا يسجل نجاح التنبيه إذا فشلت إحدى الوجهات. سجل `Telegram delivery OK target=...` دليل قبول Telegram للرسالة، وليس دليل قراءة الأعضاء.
- الحالة المستمرة محفوظة في artifact `v10-alert-state` بين دورات Actions. لا يحتاج هذا المسار polling إضافيًا.
