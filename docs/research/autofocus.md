# AUTOFOCUS — feasibility, metric read, lens drive, custom-AF design

SIGMA fp MAIN Ver.5.02, ARM32 LE, base 0xC0000000 (file off = addr - 0xC0000000).
Research only — test body has a manual (non-motorized) lens; NO drive test possible
this session. Addresses below are from static analysis of analysis/MAIN_c0000000.bin
(fw.sqlite + capstone via af_dis.py). "Confirmed" = read directly from disassembly;
"Inferred" = deduced from context / external knowledge. All singletons are in the
0xC3xxxxxx DDR region, so they are live-readable with `fpsh mem get` (two-step
deref where a heap pointer is involved).

Tooling added: `af_dis.py` — `dis <addr> [n] [t]`, `w <addr>`, and `xref <hexaddr>`
(a movw/movt A1-encoding scanner; fw.sqlite `arefs` only catches literal-pool
refs and MISSES movw/movt-built addresses, which is how most of these constants
are formed).

================================================================================
1. AF ARCHITECTURE (confirmed from source-path + class strings)
================================================================================
- AfMgr library: src/library/CameraController/AfMgr/... with sub-trees
  Measure/ (AfMgrMeasurePdafDefocus.cpp), Drive/ (AfMgrDriveBase, ...Intermittent,
  ...Scan), Process/ (Base, ContShooting, Fast, Hybrid, Near, Slow, Rocking),
  Function/ (AfMgrPhaseDetection.cpp), Old/af_single.cpp.
- Modes: SAF (single contrast AF) + CAF (continuous). CAF drive states seen in
  logs: SLOW_AF / FAST_AF / NEAR_AF / ROCKING / HYBRID (0xC091E8xx–0xC091F9xx).
- Mount focus HAL (src/hal/Lens/src/LensLmount/):
  * LmountFocusL     — native L-mount motor (LmountFocusL.cpp)
  * LmountFocusMc    — MC-21 adapter (Canon EF / Sigma SA) (LmountFocusMc.cpp)
  * LmountFocusNone  — manual / no-motor (== the lens on the test body)
  * LmountPdaf.cpp   — phase-detect update (bool LmountPdaf::update(eMountState))
- The focus metric (contrast) is computed in the ISP/AF stats path and is
  readable NOW even with the manual lens; DRIVING a lens needs LmountFocusL/Mc
  plus a motorized lens (see §4).

================================================================================
2. METRIC READ — where the contrast/focus value is and how to read it
================================================================================
The contrast metric is the ISP high-pass-filter (HPF) energy per AF area, exposed
in the firmware as "AFval" / "AfData[Max][H|L]" / "hpf_high/hpf_low". Two runtime
surfaces exist.

2a. LIVE per-frame AF working globals (BEST for a read-only metric monitor)
--------------------------------------------------------------------------------
The live CAF worker logs the current metric via the format string
  0xC0925108  '### err=%d,afpos=%4d,barea=%3d,bunkatu=%d,ccd=%d hpf=%d,%d, Oft=...
loaded at 0xC028E1CC inside the CAF worker (enclosing func ~0xC028D968). The
worker pulls its args from a fixed global struct in the 0xC32914xx–0xC32918xx
DDR block. Confirmed loads in that function (0xC028E12C–0xC028E1C4):
  0xC32914D0   ldr  (afpos / current focus position word)          [Confirmed load; field = Inferred]
  0xC32914E0   ldr                                                  [Confirmed load]
  0xC3291514   ldr                                                  [Confirmed load]
  0xC32915B8   ldr                                                  [Confirmed load]
  0xC32915F4   ldrh                                                 [Confirmed load]
  0xC329160B   ldrb (a small mode/flag byte, e.g. caf_mode/ccd)     [Confirmed load]
  0xC32915BC   (cleared to 0 after log at 0xC028E1FC)               [Confirmed store]
  0xC3291617   (cleared to 0 after log at 0xC028E20C)               [Confirmed store]
  0xC3291848   read by CafPdDriveMain at 0xC024A377 ([+0x90])       [Confirmed load]
These are the fields the '### err=...,afpos=...,hpf=%d,%d,...' logger prints, i.e.
the HPF contrast pair + afpos + caf_mode. They are plain globals: read directly
with `fpsh mem get 0xC32914D0,,4` etc. This is a manual-lens-safe way to WATCH the
contrast metric change as you rack focus by hand — ideal for the offline study
requested. (Exact field<->offset mapping within the struct is Inferred from the
format-string order; addresses of the loads are Confirmed.)

2b. Per-area CAF record buffer (full metric, incl. AfData[Max], HPF hi/lo, AFOFT)
--------------------------------------------------------------------------------
The console command `af msr cafdmp` ("dump caf running data to card") dumps a ring
of per-area records. Chain (all Confirmed):
  cmd table entry @0xC0918914 -> handler 0xC022CFF8
  handler uses CAF-recorder singleton object @ 0xC3288584 (init guard 0xC3288650),
    ctor 0xC029C488, dump 0xC029C4C0.
  Record buffer pointer = *(0xC3288584 + 8)  (heap block, size 0x0011CC44 alloc'd
    in the recorder init 0xC029CA40).
  Per-area stride = 264 bytes (0x108): index math `r0 = area + area<<5` (=area*33),
    then `<<3` (=area*264); base = buf + 4. (Confirmed at 0xC029D1A4–0xC029D1B4.)
  Confirmed field offsets within a record (from the [D]/[H] column formatter
  0xC029D190):
    +0x30  AFval / AfData[Max] low  (r2, fmt 0xC09272B8)   [Confirmed load]
    +0x34  AFval / AfData[Max] high (r3)                   [Confirmed load]
    +0x38, +0x3C, +0x40  mpos / caf_mode / drive_pulse     [Confirmed load]
    +0x48  ldrh  HPF area value                            [Confirmed load]
    +0x4C  word: low16 + (high16>>16) = HPF_high/HPF_low    [Confirmed load]
    +0x50, +0x54  AFOFT_H / AFOFT_L (offset-cancel)         [Confirmed load]
  Area count = *(*(recorder+8)) style loop bound (Confirmed loop 0xC029CA2C).
To read at runtime: `p = mem get 0xC3288584+8`; then read `p+4 + area*264 + 0x30`.
NOTE: this buffer is only maintained while CAF-data recording is enabled, so 2a is
the more reliable always-on surface.

2c. SAF area evaluation reader (console-callable metric read)
--------------------------------------------------------------------------------
  `af msr safarea` ("saf evaluation value area check") -> handler 0xC022CEC8
  uses SAF-eval singleton @ 0xC3288580 (guard 0xC3288648), ctor 0xC029BC20,
  reader 0xC029BC40 (writes result into object +0x354). (Confirmed.)
  `af msr safoffset` -> 0xC022CD68 (offset-cancel measurement).
  `af msr peak`      -> 0xC022CE18 (peak search measurement — drives + reads).

================================================================================
3. LENS-DRIVE INTERFACE — addresses and why manual blocks driving
================================================================================
3a. Native L-mount driver (LmountFocusL)
--------------------------------------------------------------------------------
  Singleton object @ 0xC3476414 (ctor accessor 0xC033DB20, init guard +0x60).
  vptr / vtable = 0xC0B8EEEC (set at ctor 0xC033DA0C). vtable layout (slot0=offset,
    slot1=typeinfo 0xC0B8EF68, virtuals from +0x08):
      +0x08 0xC0340BF8   +0x0C 0xC033D970   +0x10 0xC033D9A8
      +0x14 0xC033DBC8   +0x3C 0xC033DC10 (set-target)
      +0x44 0xC033DC40  getCurFocusPos  = reads [obj+0x58]+0x7C   [Confirmed]
      +0x48 0xC033DC58                    reads [obj+0x58]+0x80    [Confirmed]
      +0x4C group (0xC033DC70) reads [obj+0x58]+0x84/+0x88 = focus range min/max [Inferred]
  eMountState update()  = 0xC033DB60 (state-machine glue, not the physical move).
  Focus-drive task: name "focusDriveTaskProc" (0xC0B8F554) created in the
    LmountFocusL task-setup block 0xC0340260+ ; task body runs the drive loop
    (0xC0340340+) and calls the submit path.
  DRIVE SUBMIT (physical move): 0xC033FDA0, called at 0xC0340314 with
    (obj, mode=2, target, dir, ...). It fetches the lens-comm object [obj+0x58]
    and calls the L-mount command sender 0xC0356328 (0xC033FE18). (Confirmed.)
  0xC0356328 is the primitive that emits CMD_Focus to the lens — cf. the L-mount
    diagnostic string 0xC033AE94 '  Lens support CMD_Focus(drive command 5)'.
  So: custom drive = set target, then invoke 0xC033FDA0(objL, 2, target, dir, ..)
    OR drive N pulses via the AfMgr `af msr peak/defocus` handlers (§3d).

3b. MC-21 adapter driver (LmountFocusMc)
--------------------------------------------------------------------------------
  update() @ ~0xC0340D40 (assert str 0xC0B8F720 loaded at 0xC0340D54). Same shape
  as L but talks the MC-21 protocol (' mc21 spec ver' 0xC0348884, LmountMcData.cpp).
  A Canon EF / Sigma SA lens on an MC-21 is drivable through this path.

3c. Manual / no-motor driver (LmountFocusNone) — WHY DRIVING IS BLOCKED
--------------------------------------------------------------------------------
  Singleton in 0xC347xxxx region (ctor at 0xC0342270+, base ~0xC3476620).
  vtable ~0xC0B8FB90; every virtual points into the tiny 0xC0342xxx range:
    0xC0342320, 0xC0342348, 0xC0342238(update), 0xC0342378, 0xC0342388, 0xC0342358.
  These are INERT STUBS. Confirmed: LmountFocusNone::update (0xC0342238) only calls
  the trace helper 0xC035EB98 with its typeinfo strings and then `mov r0,#1; return`
  — it issues NO lens command and moves nothing. (Confirmed 0xC0342238–0xC0342268.)
  Consequence: with the manual lens the mount HAL binds LmountFocusNone, so the
  entire AfMgr->FocusDrive->Lmount chain terminates in no-op stubs. There is no
  motor and no CMD_Focus channel, so a drive request is silently accepted (returns
  true) and discarded. This is the hard block, independent of any menu setting.

3d. Higher-level drive entry points (usable to move a motorized lens)
--------------------------------------------------------------------------------
  Console: `af msr peak <amount> <dir>` -> 0xC022CE18 (contrast peak-search sweep;
    args include 'peak measurement drive amount' and 'peak measurement drive
    direction ( to inf or near )'). `af msr defocus <pulse> <dir>` -> 0xC022D100,
    `af msr cafwob` (wobble) -> 0xC022D050. dir arg: 0=Near / 1=Far
    (str 0xC091A108 'defocus drive direction.(0:Near/1:Far)').
  AfMgr drive classes: AfMgrDriveBase / AfMgrDriveIntermittent / AfMgrDriveScan
    (strings 0xC091C41C/4BC/548) — the internal drivers the AF processes use.
  PtpLensFocusDriveObserver (0xC0CF48C8) + XC_LensFocusDriveSubject (0xC0B96360)
    + SetFocusPosition command (0xC0BBC788) are the outward (PTP/GUI) drive API.

================================================================================
4. PdCaf / PHASE-DETECT — LIVE or DEAD on the fp?
================================================================================
The full phase-detect stack is PRESENT in the image (shared codebase with fp L /
other bodies):
  CalcPdCafDriveDir 0xC027EE0C, CalcCafDriveSpeed 0xC027F240,
  CafMode5_CheckPdaf 0xC0280B10 / _JudgeDrive 0xC0280BC0 / _CalcDrivePulse 0xC0281554,
  CafPdDriveMain ~0xC024A230 (tag 0xC091D8A4, ref 0xC024A3BC),
  AfMgrMeasurePdafDefocus.cpp, LmountPdaf::update, '[Defocus Per Pls]' 0xC0336DE9,
  isExecOnPdafData 0xC027C15C, 'PDAF size=%u,...line=(...)' 0xC0283B20.
ASSESSMENT: DEAD (dormant) on the original fp.
  * The dispatcher (enclosing func ~0xC02854B8) has an explicit
    '----------------------------- not use pdaf' branch (str 0xC02860D0, taken at
    0xC028612C) and '###### not drive pdaf . do near af' (0xC028601C). The branch
    is selected at RUNTIME from PDAF-data validity (e.g. [sp,#0x24]==-1 test at
    0xC0285FCC), NOT from a compile-time flag. (Confirmed.)
  * The original fp sensor has no on-sensor phase-detect pixels, so PDAF area/
    defocus data is never populated -> the 'not use pdaf' path is always taken and
    the fp runs contrast-only (its published 49-point contrast AF). [Inferred, but
    strongly supported: fp = contrast-only; fp L (2021) added hybrid PDAF. This
    matches the prior a7III-comparison note that the AF-model vtable slot returns 0.]
  Practical upshot: do NOT build on PdCaf for the fp. A custom AF must be
  contrast/hill-climb. (On an fp L the PdCaf path would be live and preferable.)

================================================================================
5. CONCRETE CUSTOM CONTRAST-AF DESIGN (read metric -> hill-climb -> drive)
================================================================================
Prereq for the DRIVE half: a motorized lens (native L, or EF/SA via MC-21). With
the manual lens ONLY the metric-read + algorithm can be exercised (open-loop log).

Algorithm (single-shot SAF hill-climb, center area):
  1. READ metric M(area) each frame from the live globals (§2a): sample the HPF
     contrast pair at 0xC32914xx (or, if CAF recording is on, buf[area]+0x30/+0x48
     via 0xC3288584). Use the center AF area index.
  2. COARSE SWEEP: drive toward Near in fixed steps (e.g. 8–16 pulses) via
     0xC033FDA0(objL=*0xC3476414, mode=2, step, dir=0) — or `af msr peak amt 0`.
     After each step wait for a fresh frame (metric latency ~1 frame) then re-read M.
  3. Track M_max and its position; when M drops below k*M_max for j consecutive
     steps -> peak passed. (Mirror of the stock '[%s] peak detect! contrast search'
     0xC027A2F0 and 'contrast reverse direction retry' 0xC027A3B0.)
  4. REVERSE + FINE: reverse dir (1), halve step, climb back to the recorded peak
     position (read current pos via vtable getter 0xC033DC40 = [objL+0x58]+0x7C).
  5. SETTLE at peak; report focus locked.

Firmware functions/addresses the loop calls:
  - Metric read : 0xC32914D0.. (globals, §2a) ; optional 0xC3288584->+8 buffer.
  - SAF eval read (clean accessor): reader 0xC029BC40 on obj 0xC3288580, or the
    console `af msr safarea` handler 0xC022CEC8.
  - Current focus pos: L-mount vtable slot +0x44 = 0xC033DC40 (reads [objL+0x58]+0x7C).
  - Focus range (limits): L-mount vtable slot 0xC033DC70 -> [objL+0x58]+0x84/+0x88.
  - Drive step  : 0xC033FDA0(objL,2,pulses,dir[,..]) -> lens sender 0xC0356328;
                  or AfMgr `af msr peak` 0xC022CE18 / `af msr defocus` 0xC022D100.
  - Frame sync  : gate on the CAF worker's per-frame update (worker ~0xC028D968;
                  the 0xC32915BC/0xC3291617 flags are cleared each cycle at
                  0xC028E1FC/0xC028E20C — usable as a "new metric ready" tick).

Integration hooks (RAM-patch, consistent with the project's open-gate approach):
  - Simplest offline study (manual lens, no drive): a background task started via
    the existing SD loader (tk_cre_tsk 0xC0016A58 / tk_sta_tsk 0xC0016BC0, stksz
    <=0x2000) that polls 0xC32914xx and logs/HUD-prints the metric so you can watch
    contrast peak by hand-racking focus. No hooks into the AF state machine needed.
  - Full closed-loop (needs motorized lens): either (a) a standalone task that
    calls the drive primitives above directly while reading the metric, bypassing
    AfMgr; or (b) hook the key observer (HybridKeyEventObserver update 0xC0269B50,
    already identified) to trigger a custom SAF sweep on a chosen button.
  - Do NOT flash; keep this RAM/sideload (see FIRMWARE-DECODE §7).

================================================================================
6. WHAT AN AF LENS CHANGES
================================================================================
- The mount HAL binds LmountFocusL (native) or LmountFocusMc (MC-21) INSTEAD of
  LmountFocusNone, so the drive vtable slots become the real senders (0xC033FDA0 ->
  0xC0356328 -> CMD_Focus) rather than the 0xC0342xxx no-op stubs. Driving becomes
  possible with zero code changes to the drive layer — it is purely lens-presence
  gated.
- Focus position getter/setter and the focus range limits (§3a) become
  meaningful (populated from the lens over CMD_Focus). The stock SAF/CAF also
  starts working, so a custom loop can either reuse `af msr peak/defocus`
  primitives or drive AfMgrDriveScan directly.
- Still contrast-only on the original fp (PdCaf stays dead, §4). An fp L body (not
  this one) would additionally light up the PdCaf/defocus path for faster,
  predictive AF.

================================================================================
CONFIRMED-vs-INFERRED SUMMARY
================================================================================
Confirmed by disassembly: all handler/singleton/vtable/drive-submit/metric-load
addresses; the LmountFocusNone no-op stub; the 'not use pdaf' runtime branch; the
per-area record stride (264) and field offsets; the CAF-recorder buffer deref.
Inferred (context / external): exact field<->name mapping inside the 0xC32914xx
struct; that the fp sensor lacks PDAF pixels (hence PdCaf dead) — supported by the
runtime 'not use pdaf' branch + published fp = contrast-only + prior a7III note.
No hardware was exercised this session (manual lens, camera offline).
