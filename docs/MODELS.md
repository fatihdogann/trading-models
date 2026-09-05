# Deterministic Model Specifications

Her modelin tanımı kod'daki gibi deterministik kurallara dayanır. Sübjektif
kavram ("güçlü mum", "kırılım güçlü" vb.) yoktur; yalnızca aşağıdaki
ölçülebilir eşikler vardır ve hepsi `configs/default.yaml`'den
konfigüre edilebilir. Bar indeksleri, bilginin **kesinleştiği** bar'ı
gösterir (lookahead yok).

---

## 0. Primitive tanımları (tüm modeller bunları kullanır)

### Confirmed Swing
- Pivot high @ `p`: `high[p]`, `[p-left, p+right]` penceresinin maksimumu
  (sağ tarafta strict, solda `>=`; eşitlikte sonraki bar pivot olur).
- **Kesinleşme: bar `p+right` kapanınca.** O ana kadar pivot yoktur.
- Etiketler: önceki aynı taraflı confirmed swing'e göre HH / HL / LH / LL.

### BOS / MSS (close-based)
- Kırılım yalnızca **close** ile: close > son kırılmamış confirmed swing
  high → trend BULL; close < ... low → trend BEAR.
- Önceki trend ile aynı yönde → `BOS`; ters yönde → `MSS` (CHoCH).
- İlk kırılım trendi kurar ve `BOS` etiketlenir. Wick kırılımları yapı
  kırılımı sayılmaz (o bir liquidity event'idir).

### Liquidity Pool + Sweep Lifecycle
- Pool kaynakları: confirmed swing'ler, EQH/EQL (tolerans `0.15 × ATR`,
  seviye = kümenin uç değeri), PDH/PDL (yerel gün boundary'sinde kesinleşir),
  PWH/PWL, önceki session H/L, (opsiyonel) canlı session ekstremleri.
- Sweep: `wick` (level ötesine dokundu, içinde kapandı — aynı bar reclaim
  sayılır) veya `close_through` (level dışında kapandı = breakout girişimi).
- `close_through` sonrası: `max_reclaim_bars` içinde içeri close →
  **reclaim**; `breakout_confirm_bars` boyunca dışarıda kalırsa →
  **confirmed_breakout** (sweep hayatı biter, pool tüketilir).
- Event alanları: type, price, formed time, sweep time, yön, bars_held,
  depth & depth/ATR, reclaim bilgisi (fiyatın level içine dönüşü).

### Displacement
`candle`, yönde ve şu üçünü sağlarsa displacement'tır:
- `|body| / ATR ≥ 1.2`
- `body / range ≥ 0.60`
- close, bar'ın uç %30'unda (`close_location ≥ 0.70`)
- ve `|body|/ATR ≥ 1.8` (strong, percentile şartını bypass eder)
  **veya** trailing 100 bar içinde `body/ATR` percentile ≥ 85.

### FVG (3-bar)
- Bullish @ `i`: `low[i] > high[i-2]`; Bearish: `high[i] < low[i-2]`.
- Kayıt: top/bottom/mid, size/ATR, ilk temas, fill oranı, full fill,
  inversion. Kendi oluşum barında mitigation sayılmaz.

### Sessions (DST-safe)
- `Asia 20:00–00:00`, `London 02:00–05:00`, `NY AM 09:30–12:00`,
  `NY 09:30–16:00` — hepsi `America/New_York` saatiyle; kill zone =
  `{london, ny_am}`. Gün boundary'si yerel gece yarısı (PDH/PDL bununla
  üretilir).

### HTF Context
- Aynı primitive'ler HTF'de çalışır; her HTF bar kapanışında snapshot.
- LTF barı `t`, yalnızca `close_time ≤ t` snapshot'ı görebilir.
- Snapshot: HTF trend, dealing range (son confirmed swing high/low),
  equilibrium ± `%2` bant → premium/discount, kırılmamış swing'ler →
  nearest external liquidity.

---

## 1. Liquidity Sweep Model

```
WAITING_LIQUIDITY → SWEPT → RECLAIMED → DISPLACEMENT → MSS_CONFIRMED
                  → TRIGGERED
her aşamadan → INVALIDATED / EXPIRED (sebep kayıtlı)
```

- Spawn: pool sweep (kind ∈ watch list, `depth_atr ≤ 1.5`; close-through
  default izinli).
- Reclaim zaten gerçekleşmemişse `max_reclaim_to_displacement_bars=8`
  içinde beklenir; gelmezse `NO_RECLAIM`.
- Reclaim'den sonra 8 bar içinde yönde displacement → yoksa
  `NO_DISPLACEMENT`.
- Displacement'tan sonra 8 bar içinde yönde BOS/MSS → yoksa `NO_MSS`.
- Entry: raid zone'un (sweep extreme ↔ level) retesti, 24 bar içinde;
  dolmazsa `EXPIRED: ENTRY_TIMEOUT`. Her an ters yönde kesin close
  extreme'ı aşarsa `CLOSED_BELOW_SWEEP_LOW` / `CLOSED_ABOVE_SWEEP_HIGH`.
- Invalidation: sweep extreme. Target: girişe en yakın kırılmamış karşı
  taraf pool'u.
- Ağırlıklar: clean_sweep 25, reclaim 20, strong_displacement 20,
  confirmed_mss 20, session_context 10, htf_alignment 5.
  Required: `reclaim`, `confirmed_mss`.

## 2. PO3 (Power of Three)

```
ACCUMULATION (session instance) → RANGE_CONFIRMED → MANIPULATION
→ DISTRIBUTION → TRIGGERED
```

- Accumulation: yapılandırılan session (default `asia`) instance'ı; range
  `[0.5, 3.5] × ATR` aralığında değilse aday hiç açılmaz
  (`RANGE_NOT_COMPRESSED` reddi).
- Manipulation: range'in bir tarafının raid'i — wick breach + içeri kabul,
  veya close-through'un reclaim penceresinde geri alınması. Yönü raid
  tarafı belirler (low raid → LONG). `manipulation_window_bars=24`.
  Range dışında tutarlı close → `RANGE_BROKEN`.
- Distribution: ters yönde displacement **ve** karşı boundary'nin close ile
  aşılması, `distribution_window_bars=24` içinde → TRIGGERED.
- Kayıt: accumulation range, manipulation side/depth/ATR, distribution
  yönü/magnitude/ATR, targeted liquidity.
- Ağırlıklar: compressed_range 25, manipulation 25, distribution 30,
  session_context 10, htf_alignment 10. Required: manipulation +
  distribution.

## 3. ICT 2022

```
HTF Bias → Liquidity Raid → Reclaim → Displacement → MSS → FVG
→ FVG Retracement → TRIGGERED (entry) → Opposing Liquidity
```

- HTF bias: snapshot trend yönü == setup yönü (`htf_required=true` iken
  HTF yoksa spawn yok). Bias'e karşı raid'lerspawn edilmez
  (`HTF_BIAS_MISMATCH` reddi).
- Sekans pencereleri: reclaim ≤ 6 bar, displacement ≤ 8 bar, MSS ≤ 8 bar,
  FVG displacement ayağında (`mss + 2` bar içi), entry beklemesi 48 bar.
- FVG seçimi: penceredeki yönlü, aktif, `size_atr ≥ 0.15` FVG'lerin en
  büyüğü. Entry: `fvg_touch` (FVG ucu) veya `fvg_ce` (midpoint); gap
  açılışında open'ten fill. Invalidation: sweep extreme.
- **Scoring (full):** htf_bias 15, external_liquidity 15, clean_sweep 20,
  strong_displacement 15, confirmed_mss 15, fvg_quality 10,
  session_context 5, smt_confirmation 5 → /100.
  **Required:** htf_bias, reclaim, displacement, confirmed_mss, fvg.
  Skor 100 olsa da required koşul eksikse setup tetiklenmez.
- **Ablation varyantları:** tetikleme aşaması değişir, checklist her
  varyantta tam hesaplanır: `sweep_only` (reclaim'de), `sweep_disp`,
  `sweep_mss`, `sweep_mss_disp`, `full` (FVG retrace'de).

## 4. Turtle Soup

```
WATCHING_LEVEL → BROKEN_OUT → RECLAIMED → CONFIRMED → TRIGGERED
```

- **Breakout ≠ sweep:** yalnızca `close_through` (close ile aşan) lifecyle
  aday açar; `allow_wick_only=false` default (wick sweep'ler zaten
  Liquidity Sweep modelinin alanı).
- Reclaim: `max_reclaim_bars=5` içinde içeri close; breakout `3` barda
  kesinleşirse `BREAKOUT_CONTINUED`.
- Onay: reclaim'den sonra 8 bar içinde ters yönde BOS/MSS (veya
  yapılandırılırsa displacement) → TRIGGERED. Confirmation'a kadar close
  breakout extreme'ı aşarsa `CLOSE_BEYOND_BREAKOUT_EXTREME`.
- Invalidation: breakout extreme. Target: ters yönde en yakın pool.
- Ağırlıklar: failed_breakout 30, quick_reclaim 20, confirmed_reversal 25,
  external_level 15, session_context 10. Required: reclaim +
  confirmed_reversal.

## 5. SMT Divergence (cross-asset)

- Karşılaştırma **corresponding confirmed swing'ler** üzerinden: B tarafının
  pivot zamanı A'nın pivotuna `max_swing_time_diff` (default 3h) içinde ve
  A'nın swing'i kesinleştiğinde B'ninkiler zaten kesinleşmiş olmalı.
- Bullish SMT: A lower low yaparken (|ΔA| anlamlı) karşılık gelen B swing
  low'u higher/equal low; `(ΔB − ΔA)/ATR ≥ min_divergence_atr (0.15)`.
  Bearish simetrik.
- Hesaplanan alanlar: magnitude (ATR), swing timing difference, rolling
  return correlation (100 bar, as-of), resolution (A'da yönde MSS →
  `structure_shift`; `48` barda gelmezse `unresolved`).
- Ağırlıklar: divergence 40, tight_timing 15, correlation 20, magnitude 15,
  resolution 10. Required: divergence. SMT event'leri ICT 2022 skoruna
  `smt_confirmation` bileşeni olarak bağlanır (±3h pencere).

---

## Outcome / Araştırma Katmanı (model management'tan ayrı)

Tetiklenen setup'lar için bar-bar ileriye bakılır (stop-first conservative):
- `r`, `mae_r`, `mfe_r`, `bars_held`, `t_target_bars`, `t_invalidation_bars`,
  exit_reason ∈ {TARGET, STOP, TIME}, `max_hold_bars=96`,
  target yoksa `default_target_r=2.0`.
- Stat tabloları: model/variant bazında n, win_rate, avg/median R,
  expectancy, MAE/MFE, süreler; dilimler: session, weekday, direction,
  liquidity_type, htf_trend, score bucket, volatility regime (ATR/close
  trailing percentile tercile).
- Ablation: `stats.ablation_table(result)` — sweep-only → full ICT 2022
  karşılaştırması.
