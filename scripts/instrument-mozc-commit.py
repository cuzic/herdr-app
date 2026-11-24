"""Add length-only commit tracing to the release-matched Mozc TIP."""
from pathlib import Path
import sys

source = Path(sys.argv[1]) / "src/win32/tip/tip_edit_session_impl.cc"
text = source.read_text(encoding="utf-8")
text = text.replace("#include <cstdint>", "#include <cstdio>\n#include <cstdint>", 1)
anchor = "HRESULT UpdatePreeditAndComposition(TipTextService* text_service,"
assert text.count(anchor) == 1
text = text.replace(anchor, r'''
// Diagnostic builds only. The receiver provides a file path in its environment.
void TraceCommit(const char* stage, size_t bytes, bool ok) {
  wchar_t path[1024];
  const DWORD path_size = GetEnvironmentVariableW(
      L"HERDR_IME_MOZC_TRACE", path, ARRAYSIZE(path));
  if (path_size == 0 || path_size >= ARRAYSIZE(path)) {
    return;
  }
  const HANDLE file = CreateFileW(path, FILE_APPEND_DATA,
                                  FILE_SHARE_READ | FILE_SHARE_WRITE,
                                  nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL,
                                  nullptr);
  if (file == INVALID_HANDLE_VALUE) {
    return;
  }
  char line[256];
  const int size = std::snprintf(
      line, sizeof(line), "tick_ms=%llu pid=%lu tid=%lu %s bytes=%zu ok=%d\r\n",
      static_cast<unsigned long long>(GetTickCount64()), GetCurrentProcessId(),
      GetCurrentThreadId(), stage, bytes, static_cast<int>(ok));
  if (size > 0 && size < sizeof(line)) {
    DWORD written = 0;
    WriteFile(file, line, static_cast<DWORD>(size), &written, nullptr);
  }
  CloseHandle(file);
}

''' + anchor)
anchor = '''  if (output.has_result()) {
    composition = CommitText(text_service, context, write_cookie,
                             std::move(composition), output);
    if (!composition) {'''
assert text.count(anchor) == 1
text = text.replace(anchor, '''  if (output.has_result()) {
    const size_t result_bytes = output.result().value().size();
    TraceCommit("before-tsf", result_bytes, true);
    composition = CommitText(text_service, context, write_cookie,
                             std::move(composition), output);
    TraceCommit("after-tsf", result_bytes, composition != nullptr);
    if (!composition) {''')
source.write_text(text, encoding="utf-8")
