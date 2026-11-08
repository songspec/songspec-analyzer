"""SongSpec Analyzer: key / BPM / chord / detune analysis as a service for songspec.org."""

ENGINE_VERSION = "0.5.0"  # bump whenever results can change: the cache is keyed on it
SCHEMA_VERSION = "1.0.0"


def analyze_file(path, options=None, data_dir=None):
    """Audio file in, analysis out (key, tempo, chords, detune, ...), no server needed. See songspec_analyzer/analyze.py."""
    from .analyze import analyze_file as run
    return run(path, options, data_dir)
