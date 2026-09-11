# Darkness and CinemaDNG playback research

Reviewed 2026-09-11. fp 5.02 MAIN, base `0xC0000000`. **Neither fix is verified.**
Static field mappings are separated below from proposed pixel-processing semantics.
See STATUS.md for actual hardware observations and GREEN-HOOK.md for corrected
preview object identities.

## Record profile mapping

Builder `0xC0439D18` copies table entries indexed by profile into a record object.
Selector mapping at `0xC043A158` indexes `0xC0BD05D4`; selector175 maps to profile122.
This is the **stock FHD/29.97 profile that open gate repurposes**, not an
open-gate-enable predicate. Any profile122 edit can also affect stock FHD/29.97.

Two float fields at the builder tail:

| Record object | Table base | Profile122 cell | Stock word |
| --- | --- | --- | --- |
| +0xFC | `0xC0BD852C` | `0xC0BD8714` | `0x40000000` (2.0f) |
| +0x100 | `0xC0BD89F4` | `0xC0BD8BDC` | `0x40000000` (2.0f) |

The stores at `0xC043A118` and `0xC043A128` establish the field origins. A value
pattern correlated with readout decimation does not establish a brightness
multiplier, H/V pairing semantics, or a linear pixel-gain consumer. Those remain
hypotheses. Neither changing one float nor changing both is proven safe/effective.

Four resize-related fields already changed by open gate:

| Object offset | Profile122 cell | Stock | Patched |
| --- | --- | --- | --- |
| +0x60 | `0xC0BD9A34` | 0x640 | 0x400 |
| +0x64 | `0xC0BE1684` | 0x640 | 0x400 |
| +0xD0 | `0xC0BD9EFC` | 0x640 | 0x400 |
| +0xD4 | `0xC0BE1B4C` | 0x640 | 0x400 |

The ratio 0x640/0x400 is 1.5625. This ratio alone does not prove non-normalizing
summing, a lost brightness gain, or the source of the green artifact. Sensor
mode/readout/exposure and later processing can also differ.

## Existing one-cell experiment, not a darkness fix

`toggle_opengate.py dark-on` retains the prior proposed change at **0xC0BD8714
only**, from 2.0f to `0x409C4000` (4.8828125f = 2 * 1.5625 squared).
No command changes the second float at 0xC0BD8BDC. The earlier instruction that
both "must" change together was unsupported and is withdrawn.

The reviewed tool requires a recognized installed payload and fully enabled
open-gate patch state before `dark-on`. `dark-off` restores the first float;
`off` also restores it. No recording-idle interlock exists: run only while idle.
Do not manually apply the second cell and assume this tool will restore it.
A camera reset without boot payloads remains the recovery path for unknown state.

## Required measurement before choosing compensation

1. Fixed scene/lighting/tripod, manual ISO/shutter/aperture, fixed white balance.
   Obtain stock and open-gate frames with the same intended exposure settings;
   stock FHD/29.97 can use selector175 too. Record actual metadata and settings.
2. Decode raw samples without auto-bright, tone curves or automatic exposure.
   Respect BlackLevel/WhiteLevel, CFA channels and crop geometry. Compare a
   corresponding unsaturated central gray patch after black subtraction.
3. Measure several frames, preserving original files. Separate preview brightness
   from recorded linear values and test whether exposure timing changed.
4. Identify the float consumer before changing it further. If a supervised
   one-cell experiment is performed, compare before/after behavior and clipping;
   do not assume a median ratio is automatically the correct register multiplier.

## Playback leads

Shell entries `0xC0405050/78/A0` dispatch through `0xC05BF2C0`.
Develop-sequence constructor `0xC03C8230` installs vtable `0xC0BA3CE0`;
`0xC0BA3CE8` is a step-table lead. Strings for CinemaDngTagReader, DngBufferInfo
and CinemaDngYuvCopier identify components, not a resolved geometry-selection API.
A previously cited `0xC3032D3A` pointer is outside the extracted MAIN range,
which ends at `0xC2F30CCA`; its role/reachability needs validation before calling
it an overlay entrypoint or executing it.

`0xC096F580` contains pairs 3856x2170, 1936x1090, 6064x4042 and 3968x2640.
Earlier direct-reference searches did not find a consumer. That does **not**
prove this table globally dead or that an edit would be a no-op: indirect,
computed and overlay references are not excluded. Conversely, table contents
alone are no reason to patch it.

The `YuvResize` string at `0xC0BA4298` is used as a failure label in function
`0xC03CB070`; nearby source references identify CinemagraphCreator.cpp in the
playback subtree. It is not established as the CinemaDNG preview producer.

## Playback hypothesis and next evidence

A mode-slot-derived size versus DNG-tag size mismatch is plausible. It has not
been demonstrated at the actual consumer. No frame-tag/format-slot selection
hook has been implemented. Even if dimensions differ, blindly changing them can
break format, pitch, crop, buffer allocation or lifetime.

Next: trace the sequence table and DngBufferInfo consumers; compare incoming
DNG metadata with chosen source/destination extent, format, pitch and allocation
at one verified point. Prefer actual recorded dimensions only after the buffer
contract is understood. A 3032x2012 tag does not uniquely prove open gate or
prove Bayer coverage, frame continuity, timing or absence of corruption.
