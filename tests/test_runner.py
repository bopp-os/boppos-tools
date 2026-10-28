import sys

from boppos import runner
from boppos.runner import LineSplitter, clean, format_progress

CURL_LINES = [
    "##O=- #     #",
    "#                                  0.6%",
    "########                           4.2%",
    "######################## 100.0%",
]


def kinds(parts):
    return [k for k, _ in parts]


def test_splitter_separates_lines_from_redraws():
    s = LineSplitter()
    parts = s.feed("==> Downloading\n10%\r50%\r100%\ndone\n")
    assert parts == [("log", "==> Downloading"), ("progress", "10%"), ("progress", "50%"), ("log", "100%"),
                     ("log", "done")]


def test_crlf_is_a_newline_even_across_chunks():
    s = LineSplitter()
    parts = s.feed("one\r") + s.feed("\ntwo\r") + s.feed("\n")
    assert parts == [("log", "one"), ("log", "two")]


def test_flush_keeps_the_last_partial_line():
    s = LineSplitter()
    assert s.feed("tail without newline") == []
    assert s.flush() == [("log", "tail without newline")]
    s.feed("bar 5%\r")
    assert s.flush() == [("progress", "bar 5%")]


def test_curl_bars_become_fixed_width_with_percent():
    shown = [format_progress(t) for t in CURL_LINES]
    assert shown[0] == ("", None)  # the initial scribble is dropped
    text, pct = shown[1]
    assert pct == 0.6 and text.endswith("  0.6%") and len(text) < 40
    assert shown[2][1] == 4.2
    full, pct = shown[3]
    assert pct == 100.0 and full.startswith("[" + "#" * runner.BAR_WIDTH + "]")


def test_other_progress_text_is_kept_and_percent_parsed():
    text, pct = format_progress("Installing org.gimp.GIMP… 45%")
    assert text == "Installing org.gimp.GIMP… 45%" and pct == 45.0
    assert format_progress("Waiting…") == ("Waiting…", None)


def test_clean_strips_ansi_and_control_characters():
    assert clean("\x1b[2K\x1b[1Ahello\x1b[0m\x07\x00") == "hello"


def test_run_stream_turns_redraws_into_progress_events():
    code = ("import sys,time;"
            "sys.stdout.write('start\\n');"
            "[ (sys.stdout.write(f'{'#'*i}   {i*25:.1f}%\\r'), sys.stdout.flush()) for i in range(1,5) ];"
            "sys.stdout.write('\\nend\\n')")
    events = []
    assert runner.run_stream([sys.executable, "-c", code], events.append) == 0
    log = [e["line"] for e in events if e["event"] == "log"]
    progress = [e for e in events if e["event"] == "progress"]
    assert log[0] == "start" and log[-1] == "end"
    assert [p["percent"] for p in progress] == [25.0, 50.0, 75.0] or progress[-1]["percent"] == 100.0
    assert not any(line.startswith("#") for line in log), "raw bar redraws must not appear as log lines"
    assert any(line.startswith("[") and line.endswith("100.0%") for line in log)  # the final bar, tidied


def test_emitter_commits_the_last_bar_once(tmp_path, capsys):
    log = tmp_path / "a.log"
    emit = runner.make_emitter(False, log)
    emit({"event": "progress", "line": "bar 10%", "percent": 10})
    emit({"event": "progress", "line": "bar 90%", "percent": 90})
    emit({"event": "log", "line": "next"})
    assert capsys.readouterr().out.splitlines() == ["bar 90%", "next"]  # not a tty: one line per bar
    assert log.read_text().splitlines() == ["bar 90%", "next"]


def test_run_stream_turns_bootc_progress_json_into_events():
    line = ('{"type":"ProgressBytes","task":"pulling","description":"Pulling","id":"x","bytesCached":0,'
            '"bytes":5,"bytesTotal":10,"stepsCached":0,"steps":1,"stepsTotal":2,"subtasks":[]}')
    code = f"print('Fetching ostree chunk'); print({line!r})"
    events = []
    assert runner.run_stream([sys.executable, "-c", code], events.append) == 0
    log = [e["line"] for e in events if e["event"] == "log"]
    assert log == ["Fetching ostree chunk", "Downloading 2 layers (10 B)"]
    assert [e["percent"] for e in events if e["event"] == "progress"] == [50]
