<div dir="rtl">

# GhostRAM Link

**استعمل رام هاتفك كطبقة Swap مشفّرة لحاسوبك (Linux) عبر كابل USB، دون Root على الهاتف، مع جلب مسبق
(Prefetching) يُخفي بطء الكابل.** مشروع بحثي مفتوح من [HexaLab](https://hexalab.app).

[English](README.md) · المقال: [hexalab.app/ar/blog/ghostram-link-phone-ram-for-your-pc](https://hexalab.app/ar/blog/ghostram-link-phone-ram-for-your-pc/)

> **الحالة: تجربة.** يعمل بأدوات Linux القياسية، والأرقام إما مقاسة أو موسومة بأنها مستنتجة، وقد يُسقط
> تطبيقاتك إن نزعت الكابل في اللحظة الخطأ. اقرأ [docs/SAFETY.md](docs/SAFETY.md) أولاً. **أنت** تجرّب
> وتقيس وتحكم، ثم شارك أرقامك.

## ما هو وما ليس هو

- **ليس** «رام مجانية». ذاكرة الهاتف لا يمكن أن تصبح رام حقيقية للحاسوب: المعالج يحتاج إلى التحكم المباشر
  في الـ DRAM، والهواتف لا تملك رابط PCIe. كل ما يُباع بعبارة «+8GB رام افتراضية» هو Swap.
- **هو** طبقة Swap بين `zram` والقرص:

```
RAM → zram (مضغوطة في الرام) → رام الهاتف عبر USB (مشفّرة) → Swap القرص
```

- **يفيد** الحواسيب المحمولة ذات الرام الملحومة 4–8GB والتخزين البطيء (eMMC أو HDD)، في الأحمال التي تكرّر
  ترتيب وصولها إلى الذاكرة (أوزان نماذج الذكاء الاصطناعي، تحميل مراحل الألعاب…).
- **لا يفيد** حاسوباً فيه NVMe (استعمل zram + Swap على NVMe)، ولا الأحمال ذات الوصول العشوائي.

## الأرقام الأساسية (صفحة 4KB)

| مكان الصفحة | زمن الاستجابة | |
|---|---|---|
| الرام | ~0.1 µs | |
| zram | 2–5 µs | مستنتج |
| NVMe | 49–83 µs | مقاس |
| **هاتف عبر USB 3 خام (FunctionFS)** | ~220 µs | مستنتج |
| eMMC حاسوب محمول | 414 µs | مقاس |
| **هاتف عبر USB 3 و TCP (هذا المستودع حالياً)** | ~550 µs | مستنتج |
| **هاتف عبر USB 2 و TCP** | ~1000 µs | مستنتج |

لم ينشر أحد بعد قياساً حقيقياً لأسطر الهاتف، والأداة [`bench/`](bench) تتيح لك أن تكون الأول.

- **المحاكاة** ([sim/](sim)): مع ملفات التنبؤ (Profiles) تصبح طبقة هاتف USB 3 أسرع حتى ~6 مرات من Swap على
  eMMC في الأحمال المتكرّرة، وأسرع بـ 30–165 مرة من Swap على HDD، لكنها **ليست أسرع من eMMC دون جلب مسبق**،
  وأبطأ بكثير من NVMe.
- **نواة Linux حقيقية** ([tests/RESULTS.md](tests/RESULTS.md)): إعادة تشغيل Profile مسجَّل جعلت الحمل
  **أسرع بـ 2.6 مرة** (من 6.38 ثانية إلى 2.42)، وProfile خاطئ جعله **أبطأ بـ 15%**.

## البدء السريع

### 1. الهاتف (Android، دون Root)

ثبّت [Termux](https://termux.dev) (نسخة F-Droid)، ثم:

```bash
pkg install python
termux-wake-lock
curl -LO https://raw.githubusercontent.com/djalaa966/ghostram-link/main/phone/ghostram_nbd.py
python ghostram_nbd.py --size 2G
```

فعّل **USB debugging** (الإعدادات ← حول الهاتف ← انقر 7 مرات على «رقم الإصدار» ← خيارات المطوّر)، ثم صِل الكابل.

### 2. قِس أولاً (أي نظام)

```bash
# على الهاتف (Termux):
curl -LO https://raw.githubusercontent.com/djalaa966/ghostram-link/main/bench/page_server.py
python page_server.py
# على الحاسوب:
adb forward tcp:10810 tcp:10810
python bench/page_client.py --label "هاتفي، كابل USB 3" --json my-phone.json
```

إن كان الوسيط أعلى من زمن قرصك، توقّف هنا: طبقة الهاتف لن تفيد هذا الجهاز.

### 3. تفعيل طبقة الهاتف (حاسوب Linux)

```bash
sudo apt install nbd-client cryptsetup adb
sudo pc/ghostram-up.sh       # adb forward + nbd + dm-crypt + swapon (أولوية 50)
sudo pc/ghostram-watch.sh    # اختياري: تفريغ تلقائي عند ضعف البطارية أو الحرارة أو امتلاء رام الهاتف
sudo pc/ghostram-down.sh     # دائماً قبل نزع الكابل: يعيد كل الصفحات إلى الحاسوب
```

### 4. ملفات التنبؤ (تجريبي)

```bash
sudo python3 profiles/ghostprofile.py record --pid $(pgrep -n my-app) --out my-app.ghostprofile
sudo python3 profiles/ghostprofile.py replay --pid $(pgrep -n my-app) --profile my-app.ghostprofile --ahead 4096
```

## أعمال سابقة (هذا ليس ادعاء اختراع)

استعارة ذاكرة جهاز آخر (Micron *Memory as a Service*، Nswap، Infiniswap، ReadyBoost) والجلب المسبق من سجلّ
التطبيق (Windows Prefetcher، TPP، 3PO) موجودة كلها. ما يضيفه المستودع هو تركيبة مفتوحة، دون Root، ومقاسة،
للهواتف العادية والحواسيب الرخيصة. التفاصيل في [docs/RESEARCH.md](docs/RESEARCH.md).

## الترخيص

MIT © 2026 HexaLab

</div>
