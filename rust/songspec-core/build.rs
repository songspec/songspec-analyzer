// Compiles Centsible's C++ core (the copy the Python service binds, service/native/centsible/core) plus a C shim, so detune and
// Centsible's key detector are the exact code the plugin and the website run. Always optimised (/O2) like the Python binding.
fn main() {
    #[cfg(feature = "centsible")]
    {
        let core = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../service/native/centsible/core");
        let mut b = cc::Build::new();
        b.cpp(true).std("c++20").opt_level(2).include(core.join("include")).include(core.join("src"));
        for f in ["analyzer.cpp", "track_features.cpp", "sections.cpp", "tempo_automation.cpp"] {
            b.file(core.join("src").join(f));
            println!("cargo:rerun-if-changed={}", core.join("src").join(f).display());
        }
        b.file("csrc/centsible_shim.cpp");
        if b.get_compiler().is_like_msvc() {
            b.define("_CRT_SECURE_NO_WARNINGS", None).flag("/EHsc");
        }
        b.warnings(false).compile("centsible_core");
        println!("cargo:rerun-if-changed=csrc/centsible_shim.cpp");
    }
}
