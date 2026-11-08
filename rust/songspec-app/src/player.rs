//! Playback like the website's player: the song, an optional click track on the beats (high click on downbeats), and "hear at
//! A440" (played faster / slower by the detune, so pitch and tempo move together, as the website's playbackRate does).

use std::num::NonZero;
use std::path::Path;
use std::time::Duration;

pub struct Player {
    _device: rodio::MixerDeviceSink,
    song: rodio::Player,
    clicks: Option<rodio::Player>,
    mixer: rodio::mixer::Mixer,
    beats: Vec<f64>,
    downbeats: Vec<f64>,
    pub index: usize,
    pub length: f64,
    pub click_on: bool,
    pub retune_on: bool,
    retune_rate: f32,
    volume: f32,
}

const CLICK_SR: u32 = 22050;

impl Player {
    pub fn open(index: usize, path: &Path, length: f64, beats: &[f64], downbeats: &[f64], detune_cents: Option<f64>) -> Result<Self, String> {
        let device = rodio::DeviceSinkBuilder::open_default_sink().map_err(|e| format!("no audio output ({e})"))?;
        let mixer = device.mixer().clone();
        let song = rodio::Player::connect_new(&mixer);
        let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
        let src = rodio::Decoder::try_from(file).map_err(|e| format!("can't play this file ({e})"))?;
        song.append(src);
        song.pause();
        Ok(Player {
            _device: device,
            song,
            clicks: None,
            mixer,
            beats: beats.to_vec(),
            downbeats: downbeats.to_vec(),
            index,
            length,
            click_on: false,
            retune_on: false,
            retune_rate: detune_cents.map(|c| 2f64.powf(-c / 1200.0) as f32).unwrap_or(1.0),
            volume: 1.0,
        })
    }

    pub fn is_playing(&self) -> bool {
        !self.song.is_paused() && !self.song.empty()
    }

    pub fn pos(&self) -> f64 {
        self.song.get_pos().as_secs_f64()
    }

    pub fn finished(&self) -> bool {
        self.song.empty()
    }

    pub fn toggle(&mut self) {
        if self.song.is_paused() {
            self.song.play();
            if let Some(c) = &self.clicks {
                let _ = c.try_seek(Duration::from_secs_f64(self.pos()));
                c.play();
            }
        } else {
            self.song.pause();
            if let Some(c) = &self.clicks {
                c.pause();
            }
        }
    }

    pub fn seek(&mut self, t: f64) {
        let d = Duration::from_secs_f64(t.clamp(0.0, self.length));
        let _ = self.song.try_seek(d);
        if let Some(c) = &self.clicks {
            let _ = c.try_seek(d);
        }
    }

    fn click_buffer(&self) -> rodio::buffer::SamplesBuffer {
        let n = (self.length * CLICK_SR as f64) as usize + CLICK_SR as usize;
        let mut buf = vec![0f32; n];
        let len = (0.05 * CLICK_SR as f64) as usize;
        for &b in &self.beats {
            let down = self.downbeats.iter().any(|d| (d - b).abs() < 0.002);
            let f = if down { 1800.0 } else { 1200.0 };
            let s0 = (b * CLICK_SR as f64) as usize;
            for i in 0..len {
                if s0 + i >= n {
                    break;
                }
                let t = i as f32 / CLICK_SR as f32;
                buf[s0 + i] += 0.5 * (-t * 90.0).exp() * (std::f32::consts::TAU * f * t).sin();
            }
        }
        rodio::buffer::SamplesBuffer::new(NonZero::new(1).unwrap(), NonZero::new(CLICK_SR).unwrap(), buf)
    }

    pub fn set_click(&mut self, on: bool) {
        self.click_on = on;
        if on && self.clicks.is_none() {
            let c = rodio::Player::connect_new(&self.mixer);
            c.append(self.click_buffer());
            c.set_speed(self.song.speed());
            c.set_volume(self.gain());
            let _ = c.try_seek(Duration::from_secs_f64(self.pos()));
            if self.song.is_paused() {
                c.pause();
            }
            self.clicks = Some(c);
        } else if !on {
            if let Some(c) = self.clicks.take() {
                c.stop();
            }
        }
    }

    /// 0 = silent, 1 = the file's own level. Slider position is squared so the low end isn't all "barely audible".
    pub fn set_volume(&mut self, v: f32) {
        self.volume = v.clamp(0.0, 1.0);
        self.song.set_volume(self.gain());
        if let Some(c) = &self.clicks {
            c.set_volume(self.gain());
        }
    }

    fn gain(&self) -> f32 {
        self.volume * self.volume
    }

    pub fn set_retune(&mut self, on: bool) {
        self.retune_on = on;
        let r = if on { self.retune_rate } else { 1.0 };
        self.song.set_speed(r);
        if let Some(c) = &self.clicks {
            c.set_speed(r);
        }
    }
}

impl Drop for Player {
    fn drop(&mut self) {
        self.song.stop();
        if let Some(c) = &self.clicks {
            c.stop();
        }
    }
}
