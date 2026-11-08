//! The three neural networks (ONNX, exported by tools/export_onnx.py), run with tract (pure Rust, CPU).
//!
//! With the `embedded-models` feature the files are compiled into the binary from rust/models/; otherwise they are read from a
//! folder. A plan is built per input shape and kept only while an analysis queue runs (`Engine::release` drops everything, so an
//! idle app holds no model memory).

use std::borrow::Cow;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use tract_onnx::prelude::*;

pub type Plan = Arc<TypedRunnableModel>;

pub struct ModelBytes {
    pub beat_this: Cow<'static, [u8]>,
    pub skey: Cow<'static, [u8]>,
    pub btc: Cow<'static, [u8]>,
    pub btc_mean: f32,
    pub btc_std: f32,
}

#[cfg(feature = "embedded-models")]
mod embedded {
    pub static BEAT_THIS: &[u8] = include_bytes!(concat!(env!("CARGO_MANIFEST_DIR"), "/../models/beat_this.onnx"));
    pub static SKEY: &[u8] = include_bytes!(concat!(env!("CARGO_MANIFEST_DIR"), "/../models/skey.onnx"));
    pub static BTC: &[u8] = include_bytes!(concat!(env!("CARGO_MANIFEST_DIR"), "/../models/btc.onnx"));
    pub static BTC_NORM: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/../models/btc_norm.json"));
}

fn norm_from_json(s: &str) -> (f32, f32) {
    let v: serde_json::Value = serde_json::from_str(s).unwrap_or_default();
    (v["mean"].as_f64().unwrap_or(0.0) as f32, v["std"].as_f64().unwrap_or(1.0) as f32)
}

impl ModelBytes {
    #[cfg(feature = "embedded-models")]
    pub fn embedded() -> Self {
        let (m, s) = norm_from_json(embedded::BTC_NORM);
        ModelBytes {
            beat_this: Cow::Borrowed(embedded::BEAT_THIS),
            skey: Cow::Borrowed(embedded::SKEY),
            btc: Cow::Borrowed(embedded::BTC),
            btc_mean: m,
            btc_std: s,
        }
    }

    pub fn from_dir(dir: &std::path::Path) -> std::io::Result<Self> {
        let (m, s) = norm_from_json(&std::fs::read_to_string(dir.join("btc_norm.json"))?);
        Ok(ModelBytes {
            beat_this: Cow::Owned(std::fs::read(dir.join("beat_this.onnx"))?),
            skey: Cow::Owned(std::fs::read(dir.join("skey.onnx"))?),
            btc: Cow::Owned(std::fs::read(dir.join("btc.onnx"))?),
            btc_mean: m,
            btc_std: s,
        })
    }
}

pub fn build_plan(bytes: &[u8], shape: &[usize]) -> TractResult<Plan> {
    let mut r = std::io::Cursor::new(bytes);
    let p = tract_onnx::onnx()
        .model_for_read(&mut r)?
        .with_input_fact(0, f32::fact(shape).into())?
        .into_optimized()?
        .into_runnable()?;
    Ok(p)
}

/// Plans by (model, shape), shared by the songs of one queue.
#[derive(Default)]
pub struct PlanCache {
    plans: Mutex<HashMap<(u8, Vec<usize>), Plan>>,
}

impl PlanCache {
    pub fn get(&self, id: u8, bytes: &[u8], shape: &[usize]) -> TractResult<Plan> {
        let key = (id, shape.to_vec());
        if let Some(p) = self.plans.lock().unwrap().get(&key) {
            return Ok(p.clone());
        }
        let p = build_plan(bytes, shape)?;
        let mut g = self.plans.lock().unwrap();
        // keep the cache small: one shape per model is the usual case (30 s chunks, fixed BTC windows)
        g.retain(|k, _| k.0 != id || id == 0 && k.1 == [1, 1500, 128]);
        g.insert(key, p.clone());
        Ok(p)
    }
    pub fn clear(&self) {
        self.plans.lock().unwrap().clear();
    }
}

pub fn run1(plan: &Plan, input: Tensor) -> TractResult<TVec<TValue>> {
    plan.run(tvec!(input.into()))
}
