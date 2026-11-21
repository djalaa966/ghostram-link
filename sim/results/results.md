# GhostRAM Link — simulation results

RAM 4096 pages, zram 4096 pages (ratio 2.5), 200000 accesses, 2.0 µs CPU per page. Slowdown = runtime ÷ runtime with unlimited RAM (1.0 = no penalty). Profile = recorded on run 1, replayed on run 2 with a different seed.

| config | llm | browser | game | database | pointer_chase |
|---|---|---|---|---|---|
| emmc_swap | 290.897× | 143.447× | 101.716× | 189.934× | 290.943× |
| zram+emmc | 289.182× | 97.589× | 77.758× | 131.73× | 289.24× |
| zram+phone_usb2 | 609.642× | 198.936× | 154.07× | 271.555× | 609.821× |
| zram+phone_usb3 | 326.13× | 106.38× | 81.966× | 145.558× | 326.232× |
| zram+phone_usb3_ffs | 131.046× | 43.367× | 33.444× | 59.102× | 131.086× |
| zram+phone_usb3+readahead | 128.309× | 52.87× | 24.115× | 194.587× | 147.235× |
| zram+phone_usb3+stride | 75.971× | 63.794× | 33.69× | 148.327× | 144.883× |
| zram+emmc+profile | 197.227× | 118.37× | 70.248× | 162.648× | 197.508× |
| zram+phone_usb3+profile | 46.039× | 83.436× | 15.147× | 565.348× | 46.203× |
| zram+phone_usb3+profile+gate | 46.039× | 101.587× | 57.007× | 151.153× | 46.203× |
| nvme_swap | 43.505× | 21.293× | 14.871× | 28.292× | 43.519× |
| zram+nvme | 43.496× | 15.079× | 11.789× | 20.328× | 43.51× |
| zram+nvme+profile | 3.578× | 6.422× | 2.229× | 36.084× | 3.571× |
| zram+nvme+profile+gate | 3.578× | 14.14× | 8.358× | 20.508× | 3.571× |
| hdd_swap | 7661.78× | 3766.835× | 2660.532× | 5006.737× | 7663.182× |
| zram+hdd | 7618.895× | 2547.037× | 2023.5× | 3460.416× | 7620.356× |
| zram+hdd+profile+gate | 7620.878× | 3472.658× | 2046.009× | 4667.387× | 7621.585× |
| zram+phone_usb3+profile+gate (hdd laptop) | 46.039× | 101.587× | 57.007× | 151.153× | 46.203× |

## Prefetch accuracy (share of prefetched pages actually used)

| config | llm | browser | game | database | pointer_chase |
|---|---|---|---|---|---|
| zram+phone_usb3+readahead | 1.0 | 0.536 | 0.978 | 0.066 | 0.201 |
| zram+phone_usb3+stride | 1.0 | 0.585 | 0.98 | 0.137 | 0.449 |
| zram+emmc+profile | 1.0 | 0.508 | 0.983 | 0.438 | 1.0 |
| zram+phone_usb3+profile | 1.0 | 0.235 | 0.961 | 0.016 | 1.0 |
| zram+phone_usb3+profile+gate | 1.0 | 0.523 | 0.983 | 0.437 | 1.0 |
| zram+nvme+profile | 1.0 | 0.221 | 0.961 | 0.016 | 1.0 |
| zram+nvme+profile+gate | 1.0 | 0.5 | 0.982 | 0.439 | 1.0 |
| zram+hdd+profile+gate | 1.0 | 0.51 | 0.983 | 0.438 | 1.0 |
| zram+phone_usb3+profile+gate (hdd laptop) | 1.0 | 0.523 | 0.983 | 0.437 | 1.0 |

## Robustness (browser workload, phone USB3 + profile)

| scenario | slowdown | lost pages | disk writes | drain (ms) | far tier demoted |
|---|---|---|---|---|---|
| unplug_no_backup | 246.323× | 6032 | 208029 | None | False |
| unplug_write_through | 300.224× | 0 | 333297 | None | False |
| revoke_write_back | 114.246× | 0 | 57026 | 1639.2 | False |
| revoke_write_through | 110.164× | 0 | 94175 | 0.0 | False |
| no_event_write_back | 101.587× | 0 | 0 | None | False |
| no_event_write_through | 101.587× | 0 | 92739 | None | False |
| throttle_static | 415.771× | 0 | 331615 | None | False |
| throttle_adaptive | 344.473× | 0 | 334751 | None | True |
