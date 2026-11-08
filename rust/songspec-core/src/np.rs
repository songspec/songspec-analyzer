//! Small numpy / Python equivalents with the same rounding and edge rules, so ported code gives the same numbers.

/// Python's round(x, n) (correctly rounded, ties to even on the exact binary value), via Rust's exact float formatting.
pub fn py_round(x: f64, n: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{:.*}", n, x).parse().unwrap()
}

/// numpy's np.round(x, decimals): multiply, round half to even, divide.
pub fn np_round(x: f64, decimals: i32) -> f64 {
    let f = 10f64.powi(decimals);
    (x * f).round_ties_even() / f
}

pub fn diff(x: &[f64]) -> Vec<f64> {
    x.windows(2).map(|w| w[1] - w[0]).collect()
}

pub fn median(x: &[f64]) -> f64 {
    let mut v = x.to_vec();
    crate::dsp::median_f64(&mut v)
}

pub fn mean(x: &[f64]) -> f64 {
    x.iter().sum::<f64>() / x.len() as f64
}

/// np.std (ddof 0)
pub fn std(x: &[f64]) -> f64 {
    let m = mean(x);
    (x.iter().map(|v| (v - m) * (v - m)).sum::<f64>() / x.len() as f64).sqrt()
}

/// np.percentile, linear interpolation.
pub fn percentile(x: &[f64], q: f64) -> f64 {
    let mut v = x.to_vec();
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = v.len();
    if n == 1 {
        return v[0];
    }
    let pos = q / 100.0 * (n - 1) as f64;
    let lo = pos.floor() as usize;
    let hi = (lo + 1).min(n - 1);
    let frac = pos - lo as f64;
    v[lo] + (v[hi] - v[lo]) * frac
}

/// np.searchsorted(a, v, side="left")
pub fn searchsorted(a: &[f64], v: f64) -> usize {
    a.partition_point(|&x| x < v)
}

/// sorted(set(np.round(x, 5))): rounded, sorted, exact duplicates removed.
pub fn unique_round5(x: &[f64]) -> Vec<f64> {
    let mut v: Vec<f64> = x.iter().map(|&t| np_round(t, 5)).collect();
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v.dedup();
    v
}

/// np.arange(start, stop, step) for floats.
pub fn arange(start: f64, stop: f64, step: f64) -> Vec<f64> {
    let n = ((stop - start) / step).ceil().max(0.0) as usize;
    (0..n).map(|i| start + i as f64 * step).collect()
}

/// np.histogram(values, bins=edges, weights): bin i holds edges[i] <= v < edges[i+1], the last bin also v == edges[-1].
pub fn histogram(values: &[f64], weights: &[f64], edges: &[f64]) -> Vec<f64> {
    let nb = edges.len() - 1;
    let mut h = vec![0f64; nb];
    for (&v, &w) in values.iter().zip(weights) {
        if v < edges[0] || v > edges[nb] {
            continue;
        }
        let mut i = edges.partition_point(|&e| e <= v);
        if i > nb {
            i = nb;
        }
        let i = if v == edges[nb] { nb - 1 } else { i - 1 };
        h[i] += w;
    }
    h
}

/// np.convolve(h, np.ones(2k + 1), mode="same")
pub fn box_same(h: &[f64], k: usize) -> Vec<f64> {
    let n = h.len() as isize;
    (0..n)
        .map(|i| {
            let mut s = 0.0;
            for j in (i - k as isize)..=(i + k as isize) {
                if j >= 0 && j < n {
                    s += h[j as usize];
                }
            }
            s
        })
        .collect()
}

/// np.argmax: the first maximum.
pub fn argmax(x: &[f64]) -> usize {
    let mut b = 0;
    for i in 1..x.len() {
        if x[i] > x[b] {
            b = i;
        }
    }
    b
}

pub fn argmin(x: &[f64]) -> usize {
    let mut b = 0;
    for i in 1..x.len() {
        if x[i] < x[b] {
            b = i;
        }
    }
    b
}

/// np.polyfit(x, y, 1) -> (slope, intercept)
pub fn polyfit1(x: &[f64], y: &[f64]) -> (f64, f64) {
    let n = x.len() as f64;
    let (mx, my) = (x.iter().sum::<f64>() / n, y.iter().sum::<f64>() / n);
    let (mut sxy, mut sxx) = (0.0, 0.0);
    for (a, b) in x.iter().zip(y) {
        sxy += (a - mx) * (b - my);
        sxx += (a - mx) * (a - mx);
    }
    let a = sxy / sxx;
    (a, my - a * mx)
}

/// np.linspace(a, b, n)
pub fn linspace(a: f64, b: f64, n: usize) -> Vec<f64> {
    if n == 1 {
        return vec![a];
    }
    let step = (b - a) / (n - 1) as f64;
    (0..n).map(|i| if i == n - 1 { b } else { a + step * i as f64 }).collect()
}
