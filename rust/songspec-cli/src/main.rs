//! songspec-cli FILE... : decode each file and print what the engine reads, as JSON (one object per line).

use std::path::Path;

fn main() {
    let files: Vec<String> = std::env::args().skip(1).collect();
    for f in &files {
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
