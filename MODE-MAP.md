# IMX410 sensor mode map — all 70 modes, ranked by readout quality

Source: firmware register table `0xC0B59Exx` (verified) + fpSup's decoded
`analysis_imx410/*.csv`. Field offsets confirmed against MAIN. RAM-only unlock;
power-cycle reverts.

## The quality axis = SAMPLING pattern (fields +0x44/+0x48/+0x4c/+0x50)
- **FULL `1,1,1,1`** — every photosite read. Sharpest; oversamples to UHD/4K. 23 modes.
- **2x2 `2,2,2,2`** — discards 3 of 4 photosites. Soft. This is **M117 OpenGate**
  AND **every 3K/2K CinemaDNG preset** (M27/58/106/103/88...). 36 modes.
- **3x2 / 3x3** — heavier subsample; the 2016-wide high-fps modes. 11 modes.

Your current "UHD" is almost certainly **M7 (6064x3412 FULL @30, slot 0)** — already
full-readout 6K downsampled, which is why nothing 2x2 beats it.

## What actually beats UHD (full-readout, dormant or upgrade)
| pick | mode | why | unlock |
|---|---|---|---|
| easiest | **M6** 4176x2174 **14-bit** FULL @30 (476 MB/s) | 4K, +2 bits DR over M102 | CLEAN swap slot13 (caveat: 14-bit recorder unproven) |
| best 12-bit 4K+ | **M130** 3968x2640 FULL @40 (618 MB/s) | 4K 3:2, 40fps, full readout | picker + geom hook |
| max res | **M3** 6064x4042 FULL @30 (1102 MB/s) | full 6K 3:2 | picker + geom hook, SSD burst |
| 6K60 | **M10** 6064x2022 FULL @60 (1102 MB/s) | 6K wide, 60p, 12.5ms RS | picker + geom hook, SSD burst |
| 6K @39 | **M97** 6064x4042 FULL @39 (1440 MB/s) | full 6K 3:2 fast | geom hook, burst only |

## Unlock mechanics
`build_modeswap_autorun.py <slot> <mode>` repoints a picker slot to any mode.
- **CLEAN** (target raster == slot raster): picker id only, no geometry hook.
  Only slot 0 (6064x3412) and slot 13 (4176x2174) carry full-readout rasters;
  the only *dormant* full modes matching them are the 14-bit variants (M6/100/122
  into slot 13).
- **GEOM HOOK** (raster differs): also needs the record-geometry hook
  (`build_gated_autorun` / `rowpatch_gated.S` retargeted to the new raster) + VMAX,
  else it crops/greens like OpenGate. This is the path for M130/M3/M10/M97.

CAVEAT: 14-bit full modes (M0/6/95/100/122/129/141/144) are highest quality but the
CinemaDNG recorder is only proven at 12-bit — test, don't assume.

## Full table (sorted by resolution)
```
mode      raster    AR   MP    fps   RSms bit  MB/s    readout  status      swap
--------------------------------------------------------------------------------
M97    6064x4042   1.5 24.5   39.2  24.98  12  1440  FULL(1:1) dormant geom-hook
M3     6064x4042   1.5 24.5   30.0  24.98  12  1102  FULL(1:1) dormant geom-hook
M121   6064x4042   1.5 24.5   25.0  24.98  12   919  FULL(1:1) dormant geom-hook
M0     6064x4042   1.5 24.5   19.1  51.14  14   820  FULL(1:1) dormant geom-hook
M141   6064x4042   1.5 24.5   18.0  51.14  14   772  FULL(1:1) dormant geom-hook
M142   6064x4042   1.5 24.5   18.0  24.98  12   661  FULL(1:1) dormant geom-hook
M7     6064x3412 1.777 20.7   30.0  21.09  12   930  FULL(1:1)    menu     slot0
M123   6064x3412 1.777 20.7   25.0  21.32  12   776  FULL(1:1)    menu     slot0
M101   6064x3412 1.777 20.7   24.0  21.09  12   744  FULL(1:1)    menu     slot0
M151   6064x3412 1.777 20.7   24.0  21.09  12   745  FULL(1:1)    menu     slot0
M10    6064x2022 2.999 12.3   59.9   12.5  12  1102  FULL(1:1) dormant geom-hook
M95    5712x3216 1.776 18.4   24.0  40.69  14   771  FULL(1:1) dormant geom-hook
M6     4176x2174 1.921  9.1   30.0  27.51  14   476  FULL(1:1) dormant    slot13
M102   4176x2174 1.921  9.1   30.0  13.44  12   409  FULL(1:1)    menu    slot13
M122   4176x2174 1.921  9.1   25.0  27.51  14   398  FULL(1:1) dormant    slot13
M124   4176x2174 1.921  9.1   25.0  13.59  12   340  FULL(1:1)    menu    slot13
M100   4176x2174 1.921  9.1   24.0  27.51  14   381  FULL(1:1) dormant    slot13
M108   4176x2174 1.921  9.1   24.0  13.44  12   326  FULL(1:1)    menu    slot13
M152   4176x2174 1.921  9.1   24.0  13.44  12   326  FULL(1:1)    menu    slot13
M130   3968x2640 1.503 10.5   39.3  16.32  12   618  FULL(1:1) dormant geom-hook
M129   3968x2640 1.503 10.5   19.2   33.4  14   352  FULL(1:1) dormant geom-hook
M144   3968x2640 1.503 10.5   18.0   33.4  14   330  FULL(1:1) dormant geom-hook
M145   3968x2640 1.503 10.5   18.0  16.32  12   282  FULL(1:1) dormant geom-hook
M11    3032x2012 1.507  6.1  105.4   9.22  12   965        2x2 dormant geom-hook
M117   3032x2012 1.507  6.1   99.9   9.22  12   914        2x2 dormant geom-hook
M98    3032x2012 1.507  6.1   77.3  12.44  12   708        2x2 dormant geom-hook
M143   3032x2012 1.507  6.1   18.0  12.44  12   165        2x2 dormant geom-hook
M58    3032x1708 1.775  5.2  119.9   7.83  12   931        2x2    menu     slot4
M89    3032x1708 1.775  5.2  100.0   8.54  12   776        2x2    menu     slot4
M27    3032x1708 1.775  5.2   59.9  10.56  12   465        2x2    menu     slot4
M104   3032x1708 1.775  5.2   59.9   7.83  12   465        2x2 dormant     slot4
M115   3032x1708 1.775  5.2   50.0  10.68  12   389        2x2    menu     slot4
M135   3032x1708 1.775  5.2   48.0  10.56  12   372        2x2 dormant     slot4
M220   3032x1708 1.775  5.2   48.0  10.56  12   372        2x2    menu     slot4
M106   3032x1708 1.775  5.2   30.0  10.56  12   232        2x2    menu     slot4
M111   3032x1708 1.775  5.2   30.0   7.83  12   232        2x2 dormant     slot4
M125   3032x1708 1.775  5.2   25.0  10.68  12   194        2x2    menu     slot4
M127   3032x1708 1.775  5.2   25.0   8.54  12   194        2x2 dormant     slot4
M109   3032x1708 1.775  5.2   24.0  10.56  12   186        2x2    menu     slot4
M113   3032x1708 1.775  5.2   24.0   7.83  12   186        2x2 dormant     slot4
M218   3032x1708 1.775  5.2   24.0  10.56  12   186        2x2    menu     slot4
M103   2088x1174 1.779  2.5  119.9   5.38  12   441        2x2    menu    slot17
M132   2088x1174 1.779  2.5  119.9   7.26  12   441        2x2 dormant    slot17
M118   2088x1174 1.779  2.5  100.0   5.87  12   368        2x2    menu    slot17
M133   2088x1174 1.779  2.5  100.0   7.34  12   368        2x2 dormant    slot17
M88    2088x1174 1.779  2.5   59.9   7.26  12   220        2x2    menu    slot17
M105   2088x1174 1.779  2.5   59.9   5.38  12   220        2x2 dormant    slot17
M116   2088x1174 1.779  2.5   50.0   7.34  12   184        2x2    menu    slot17
M136   2088x1174 1.779  2.5   48.0   7.26  12   176        2x2 dormant    slot17
M221   2088x1174 1.779  2.5   48.0   7.26  12   176        2x2    menu    slot17
M107   2088x1174 1.779  2.5   30.0   7.26  12   110        2x2    menu    slot17
M112   2088x1174 1.779  2.5   30.0   5.38  12   110        2x2 dormant    slot17
M126   2088x1174 1.779  2.5   25.0   7.34  12    92        2x2    menu    slot17
M128   2088x1174 1.779  2.5   25.0   5.87  12    92        2x2 dormant    slot17
M110   2088x1174 1.779  2.5   24.0   7.26  12    89        2x2    menu    slot17
M114   2088x1174 1.779  2.5   24.0   5.38  12    89        2x2 dormant    slot17
M219   2088x1174 1.779  2.5   24.0   7.26  12    89        2x2    menu    slot17
M56    2016x1344   1.5  2.7  119.9   6.16  12   488    3,2,3,3 dormant geom-hook
M147   2016x1344   1.5  2.7  100.2   7.37  12   408    3,2,3,3 dormant geom-hook
M150   2016x1344   1.5  2.7   89.9   6.16  12   365    3,2,3,3 dormant geom-hook
M149   2016x1344   1.5  2.7   80.0   6.16  12   325    3,2,3,3 dormant geom-hook
M148   2016x1344   1.5  2.7   70.0   6.16  12   285    3,2,3,3 dormant geom-hook
M139   2016x1344   1.5  2.7   59.9   8.31  12   244    3,2,3,3 dormant geom-hook
M8     2016x1344   1.5  2.7   59.9   6.16  12   244    3,2,3,3 dormant geom-hook
M134   2016x1136 1.775  2.3  119.9   7.02  12   411    3,2,3,3 dormant geom-hook
M12     2016x672   3.0  1.4  239.8   3.08  12   488    3,3,3,6 dormant geom-hook
M140    2016x672   3.0  1.4   59.9   4.15  12   121    3,3,3,6 dormant geom-hook
M9      2016x672   3.0  1.4   59.9   3.08  12   121    3,3,3,6 dormant geom-hook
M131   1984x1320 1.503  2.6   78.2   8.16  12   308        2x2 dormant geom-hook
M146   1984x1320 1.503  2.6   18.0   8.16  12    71        2x2 dormant geom-hook
```
