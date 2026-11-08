//! songspec-cli FILE... : decode each file and print what the engine reads, as JSON (one object per line).
//! songspec-cli --dsp-check FILE OUTDIR : dump the DSP stages of a 30 s excerpt for eval/rust_dsp_check.py.

use std::io::Write;
use std::path::Path;

use songspec_core::dsp;

fn dump(dir: &Path, name: &str, data: &[f32], shape: &[usize]) {
    let mut f = std::fs::File::create(dir.join(format!("{name}.f32"))).unwrap();
    let bytes: Vec<u8> = data.iter().flat_map(|v| v.to_le_bytes()).collect();
    f.write_all(&bytes).unwrap();
    std::fs::write(dir.join(format!("{name}.json")), serde_json::to_string(shape).unwrap()).unwrap();
}

fn dsp_check(file: &str, out: &str) {
    let dir = Path::new(out);
    std::fs::create_dir_all(dir).unwrap();
    let a = songspec_core::audio::decode_file(Path::new(file)).unwrap();
    let mono = a.mono();
    let sr = a.sr as f64;
    let (s0, s1) = ((30.0 * sr) as usize, (60.0 * sr) as usize);
    let x = &mono[s0..s1.min(mono.len())];
    dump(dir, "x", x, &[x.len()]);
    let t = std::time::Instant::now();
    let y22 = dsp::resample_hq(x, sr, 22050.0, false);
    eprintln!("resample_hq {:?}", t.elapsed());
    dump(dir, "y22", &y22, &[y22.len()]);
    let t = std::time::Instant::now();
    let yh = dsp::hpss_signal(&y22, 2.0, true);
    eprintln!("harmonic {:?}", t.elapsed());
    dump(dir, "harm22", &yh, &[yh.len()]);
    let t = std::time::Instant::now();
    let yp = dsp::hpss_signal(x, 2.0, false);
    eprintln!("percussive {:?}", t.elapsed());
    dump(dir, "perc", &yp, &[yp.len()]);
    let env = dsp::onset_strength(&yp, sr, 2048, 256, 1, 3, dsp::Aggregate::Median, 128);
    dump(dir, "env_perc", &env, &[env.len()]);
    let full = dsp::onset_strength(x, sr, 2048, 256, 1, 1, dsp::Aggregate::Mean, 128);
    dump(dir, "env_full", &full, &[full.len()]);
    let fine = dsp::onset_strength(&x[..(sr as usize)], sr, 512, 32, 1, 1, dsp::Aggregate::Mean, 40);
    dump(dir, "env_fine", &fine, &[fine.len()]);
    let (bt, on, local, cum, back) = dsp::beat_track_debug(&env, sr, 256, 120.0, 80.0);
    let beats: Vec<f32> = bt.iter().map(|&b| b as f32).collect();
    dump(dir, "bt_on", &on, &[on.len()]);
    dump(dir, "bt_local", &local, &[local.len()]);
    let cumf: Vec<f32> = cum.iter().map(|&v| v as f32).collect();
    dump(dir, "bt_cum", &cumf, &[cum.len()]);
    let backf: Vec<f32> = back.iter().map(|&b| b as f32).collect();
    dump(dir, "bt_back", &backf, &[backf.len()]);
    dump(dir, "beats", &beats, &[beats.len()]);
    let t = std::time::Instant::now();
    let c = dsp::cqt(&yh, 22050.0, 512, 32.70319566257483, 252, 36, 0.1);
    eprintln!("cqt 252 {:?}", t.elapsed());
    dump(dir, "cqt252", &c.mag, &[c.n_frames, c.n_bins]);
    let b = dsp::cqt(&y22[..(10.0 * 22050.0) as usize], 22050.0, 2048, 32.70319566257483, 144, 24, 0.05);
    dump(dir, "cqt144", &b.mag, &[b.n_frames, b.n_bins]);
    let r = dsp::resample_torch(x, a.sr, 22050);
    dump(dir, "torch22", &r, &[r.len()]);
    println!("{}", serde_json::json!({"sr": a.sr}));
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(|s| s.as_str()) == Some("--dsp-check") {
        return dsp_check(&args[1], &args[2]);
    }
    for f in &args {
        let t0 = std::time::Instant::now();
        match songspec_core::audio::decode_file(Path::new(f)) {
            Ok(a) => {
                let mono = a.mono();
                let t = songspec_core::centsible::analyze_tuning(&mono, a.sr as f64);
                println!(
                    "{}",
                    serde_json::json!({"file": f, "sr": a.sr, "channels": a.channels, "duration": a.duration(),
                        "tuning_valid": t.valid, "cents": t.cents, "ci": t.ci_cents, "seconds": t0.elapsed().as_secs_f64()})
                );
            }
            Err(e) => println!("{}", serde_json::json!({"file": f, "error": e.to_string()})),
        }
    }
}
