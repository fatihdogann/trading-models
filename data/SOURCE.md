# Veri Kaynağı

| Dosya | Enstrüman | Aralık | Bar | Kaynak |
|---|---|---|---|---|
| `XAUUSD_15m.csv` | Gold (COMEX `GC=F`, spot proxy) | son ~60 gün, 15m | ~4.5k | Yahoo Finance |
| `XAUUSD_1h.csv`  | Gold (COMEX `GC=F`, spot proxy) | aynı pencere, 1h | ~1.1k | Yahoo Finance |

- Timestamp'ler **UTC**, tz-aware ISO format.
- Yalnızca **kişisel araştırma/test** kullanımı için dahil edilmiştir;
  redistribution hakkı yoktur (veri sağlayıcının kullanım koşullarına bakın).
- Canlı araştırma için kendi broker/veri sağlayıcınızdan indirdiğiniz
  XAUUSD spot verisini aynı formatta (`time,open,high,low,close`)
  kullanabilirsiniz; motor enstrüman-bağımsızdır (tüm eşikler ATR-normalize).
