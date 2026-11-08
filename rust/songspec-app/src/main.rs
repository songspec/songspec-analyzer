#![windows_subsystem = "windows"]
//! SongSpec Analyzer desktop app.

fn main() -> eframe::Result<()> {
    eframe::run_simple_native("SongSpec Analyzer", eframe::NativeOptions::default(), |ctx, _frame| {
        eframe::egui::CentralPanel::default().show(ctx, |ui| {
            ui.heading(format!("SongSpec Analyzer {}", songspec_core::ENGINE_VERSION));
        });
    })
}
