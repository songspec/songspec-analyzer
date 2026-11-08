// The app icon: assets/icon.ico into the .exe (Explorer, taskbar), assets/icon_256.png for the window (tools/make_icon.py makes both
// from assets/icon.png). Without them the app builds with the default icon.
fn main() {
    println!("cargo:rerun-if-changed=assets/icon.ico");
    println!("cargo:rerun-if-changed=assets/icon_256.png");
    println!("cargo::rustc-check-cfg=cfg(has_icon)");
    if std::path::Path::new("assets/icon_256.png").exists() {
        println!("cargo:rustc-cfg=has_icon");
    }
    if std::path::Path::new("assets/icon.ico").exists() && std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut res = winresource::WindowsResource::new();
        res.set_icon("assets/icon.ico");
        res.set("ProductName", "SongSpec Analyzer");
        res.set("FileDescription", "SongSpec Analyzer");
        res.compile().expect("embedding the icon");
    }
}
