# Trading Model Engine (TME)

Fiyat davranışını **event'lere dönüştüren**, modelleri **state-machine olarak
tanımlayan** ve hangi koşul kombinasyonlarının gerçekten edge taşıdığını
ölçebilen kişisel trading research engine.

Hedef bir "ICT indicator collection" değil. İlke:

> **Önce primitive → sonra model → sonra confluence → en son signal.**

## Tasarım garantileri

- **Lookahead / repaint yok.** Swing pivot'u `right` bar kapanmadan yok;
  HTF bilgi ancak HTF bar kapandıktan sonra görünür; her event hem bar
  indeksi hem timestamp taşır. `tests/test_no_lookahead.py` verinin
  prefix'i ile tam serisini birebir karşılaştırarak bunu kanıtlar.
- **Backtest == live detection.** Motor bar-bar akar (`MarketContext.step`);
  canlı tespit de aynı kod yolunu kullanır.
- **Deterministic.** Hiçbir modelde magic number yok; tüm eşikler
  `configs/default.yaml` + `tme/config.py`'de. Eşikler ATR-normalize
  olduğundan enstrüman/fiyat ölçeğinden bağımsızdır.
- **Scoring ≠ validation.** Zorunlu (`required`) koşullar gerçekleşmeden
  setup skor yüksek olsa da tetiklenmez.
- **Timezone/DST güvenli.** Session'lar IANA timezone (varsayılan
  `America/New_York`) ile çözülür; sabit UTC offset kullanılmaz.
- **Aynı primitive her yerde.** Swing/structure/liquidity/FVG bir kez
  implement edilir; modeller sadece tüketir.

## Mimari

```
src/tme/
├── types.py        # event objeleri (Swing, SweepEvent, FVG, SetupEvent, ...)
├── config.py       # tüm threshold'lar + ağırlıklar (YAML override destekli)
├── core/           # PRIMITIVES
│   ├── atr.py          # Wilder ATR
│   ├── swings.py       # confirmed swing high/low + HH/HL/LH/LL etiketleri
│   ├── structure.py    # close-based BOS / MSS (CHoCH), trend durumu
│   ├── liquidity.py    # pool kaydı + sweep lifecycle (wick/close_through →
│   │                   #  reclaim | confirmed_breakout), EQH/EQL, PDH/PDL,
│   │                   #  PWH/PWL, prev-session H/L
│   ├── displacement.py # body/ATR, body/range, close location, percentile rank
│   ├── fvg.py          # 3-bar FVG + mitigation (first touch, fill, full fill)
│   ├── sessions.py     # DST-safe session clock + gün/hafta aggregate
│   ├── htf.py          # HTF snapshot timeline: trend, dealing range,
│   │                   #  premium/discount, nearest external liquidity
│   └── context.py      # bar-bar orkestrasyon (tek doğruluk kaynağı)
├── models/         # STATE MACHINES (sadece primitive tüketir)
│   ├── liquidity_sweep.py  SWEPT → RECLAIMED → DISPLACEMENT → MSS → TRIGGERED
│   ├── po3.py              RANGE_CONFIRMED → MANIPULATION → DISTRIBUTION
│   ├── ict2022.py          bias → raid → reclaim → disp → MSS → FVG → entry
│   ├── turtle_soup.py      BROKEN_OUT → RECLAIMED → CONFIRMED (failed breakout)
│   └── smt.py              cross-asset confirmed-swing divergence
├── scoring.py      # ağırlıklı checklist; required kapıları ayrı
├── outcome.py      # R / MAE / MFE / time-to-target-invalidation (ayrı modül)
├── backtest.py     # bar-bar replay motoru + ablation varyantları
├── stats.py        # win rate, expectancy, bucket tabloları, ablation
├── explain.py      # ✓/✗ checklist render + red/invalidasyon sebepleri
└── export.py       # DataFrame / JSONL / TradingView CSV
```

## Hızlı başlangıç

```bash
uv venv && uv pip install ".[dev]"
.venv/bin/python -m pytest tests/ -q                 # testler (31)

# hazır örnek veri ile (aşağıdaki Veri bölümüne bakın)
.venv/bin/python scripts/run_research.py \
    --csv data/XAUUSD_15m.csv --symbol XAUUSD --tf 15m \
    --htf-csv data/XAUUSD_1h.csv --htf-tf 1h --out out

# sentetik veri ile makine testi
.venv/bin/python scripts/run_research.py --demo
```

### Kendi verinle

```bash
.venv/bin/python scripts/run_research.py \
    --csv data/BTCUSD_15m.csv --symbol BTCUSD --tf 15m \
    --htf-csv data/BTCUSD_1h.csv --htf-tf 1h \
    --tz America/New_York --out out
```

- **CSV formatı:** `time, open, high, low, close` başlıklı (volume
  opsiyonel); TradingView *Export bar data* çıktısı doğrudan çalışır.
- **`--tz`:** timestamp'ler UTC değilse (ör. TradingView exportu borsa
  saatindeyse) IANA adı verin: `--tz America/New_York`. UTC ise gerekmez.
  `--htf-resample` ile 15m dosyasından 1h HTF üretebilirsiniz.
- Motor bozuk bar (high/low kuralları ihlal eden) görürse bar indeksiyle
  reddeder — verinizi temizleyin.

## Veri

`data/` içinde 60 günlük gerçek XAUUSD (COMEX gold futures `GC=F`, spot
proxy) sample'ı var — 15m (4.5k bar) + 1h (1.1k bar), UTC, Yahoo Finance'den
indirilmiş, yalnızca kişisel araştırma kullanımı için. Yeniden
indirmek/güncellemek için:

```bash
uv pip install yfinance
.venv/bin/python -c "
import yfinance as yf, pandas as pd

def clean(df):
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy(); df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[['open','high','low','close']].dropna()
    df = df[~df.index.duplicated(keep='first')].sort_index()
    df.index = df.index.tz_convert('UTC').rename('time')
    return df.round(3)

yf.download('GC=F', period='59d', interval='15m', progress=False, auto_adjust=True).pipe(lambda d: clean(d)).to_csv('data/XAUUSD_15m.csv')
yf.download('GC=F', period='90d', interval='1h',  progress=False, auto_adjust=True).pipe(lambda d: clean(d)).to_csv('data/XAUUSD_1h.csv')
"
```

## Örnek çıktı (gerçek XAUUSD, 60 gün)

```
=== TME run: 3599 setup events, 514 triggered-with-outcome ===

--- ICT 2022 ablation (does the sequence add edge?) ---
       variant  n_setups  win_rate  avg_r  median_r
          full        23     0.435 -0.191    -0.496
    sweep_disp        51     0.510 -0.101     0.024
     sweep_mss        38     0.447 -0.203    -0.382
    sweep_only       248     0.315  0.123    -1.000
```

Red sebepleri de raporlanır — kara kutu yok:

```
ict2022#full:    1006 rejected (HTF_BIAS_MISMATCH=987, POOL_KIND_NOT_WATCHED=19)
turtle_soup:      583 rejected (WICK_SWEEP_NOT_BREAKOUT=583)   # sweep ≠ breakout
po3:               43 rejected (RANGE_NOT_COMPRESSED(4.85 ATR)=1, ...)
```

## Setup çıktısı (SetupEvent)

`model, variant, direction, symbol, timeframe, start_time, trigger_time,
liquidity_type, liquidity_price, sweep_price, sweep_kind, sweep_depth_atr,
displacement_body_atr, mss_price, fvg_low, fvg_high, entry_zone, entry_price,
invalidation, target_type, target_price, score, checklist, state,
failure_reason, session, weekday, htf_trend, atr_regime` — backtest, journal,
alert, dataset ve ML feature olarak doğrudan kullanılabilir.

Üç state izlenir: `TRIGGERED`, `INVALIDATED` (nedeniyle:
`NO_DISPLACEMENT`, `CLOSED_BELOW_SWEEP_LOW`, `RANGE_BROKEN`, ...) ve
`EXPIRED` (entry gelmedi). Rapor dosyaları sembol adıyla yazılır
(ör. XAUUSD çalıştırmasında):

| Dosya | İçerik |
|---|---|
| `out/XAUUSD_report.csv` | **Ana rapor** — her setup bir satır + R/MAE/MFE sonuçları; Excel/Numbers ile açılır |
| `out/XAUUSD_tradingview.csv` | Sadece tetiklenen setup'lar: `time, model, direction, score, entry, stop, target` |
| `out/XAUUSD_setups.jsonl` | Tam structured event dump (checklist dahil) — journal/ML dataset |

## Model spesifikasyonları

Deterministic tanımlar, state makineleri, varsayılan ağırlıklar ve
invalidasyon sebepleri için: [docs/MODELS.md](docs/MODELS.md)

## Yol haritası / kapsam

İlk sürümde 5 model var: Liquidity Sweep, PO3, ICT 2022, Turtle Soup, SMT
Divergence. Silver Bullet / Unicorn / MMBM-MMSM bilinçli olarak ayrı model
olarak eklenmedi; mimari yeni model eklemeye uygun (yeni bir state-machine
modeli + config yeterli). Trade/risk management detection'dan ayrı
(`tme/outcome.py` yalnızca araştırma-grade R istatistiği üretir).

---

**Mehmet Fatih Doğan**
📧 [mehmetfatihdogann5@gmail.com](mailto:mehmetfatihdogann5@gmail.com)
