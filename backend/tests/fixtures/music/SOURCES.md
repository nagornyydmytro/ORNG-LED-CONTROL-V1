# Legal music clips (home tempo checks)

Not a USB DJ feed. Not a venue PA. Not a calibration of the estimator to one track.

Ground truth is the **author's published tempo / form**, never `inspect_tempo`.

| id | role | source | license | independent markup |
|---|---|---|---|---|
| dance_120_emsoundmoves | even dance rhythm | [Freesound 148620](https://freesound.org/people/emsoundmoves/sounds/148620/) “Drumloop 003 tempo 120.wav” by emsoundmoves | CC0-1.0 | Filename/tags `120bpm`. Page: 8.000 s, 4 measures of 4/4 → 16 beats / 8 s = **120 BPM**. |
| house_120_waveplay_035 | dense bed | [Freesound 546251](https://freesound.org/people/waveplaySFX/sounds/546251/) “[Drumloop] 120 bpm edm drum loop 035” by waveplaySFX | CC0-1.0 | Author title/tags `120bpm`. Page duration 8.000 s. Different piece from 148620. |
| sparse / transition | same CC0 dance loop, edited | derived from 148620 | CC0-1.0 | Same 120 BPM grid. Sparse = short onsets + silence. Transition = loop, then ≥3.5 s silence, then loop. |

Audio is fetched at test time into `%LOCALAPPDATA%\ORNG-LED-CONTROL\cache\music\` (Freesound MP3 preview → 44.1 kHz mono WAV via ffmpeg). The original 16-bit WAV is not mirrored in git.

## Limitations

- Previews are lossy and may be slightly shorter than the stated WAV duration.
- Stereo is folded to mono.
- Gain and dropout edits are ours; they do not change the published BPM of the sounding bars.
- A pad/drone with **no** published beat grid is not scored as a BPM target.
- This does not replace a physical USB input or a club PA.
