#include <json/reader.h>
#include <json/value.h>

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <string_view>

int main(int argc, char** argv) {
  if (argc != 2) {
    std::fprintf(stderr, "Usage: %s <poc.bin>\n", argv[0]);
    return 1;
  }
  std::ifstream f(argv[1], std::ios::binary);
  if (!f) { std::perror("open"); return 1; }
  std::ostringstream ss;
  ss << f.rdbuf();
  std::string raw = ss.str();


  // selector byte. Strip it so jsoncpp sees bare JSON text.
  std::string as_text = raw;
  if (!raw.empty() && raw[0] != '[' && raw[0] != '{' && raw[0] != '"') {
    as_text = raw.substr(1);
  }
  size_t end_arr = as_text.find(']');
  size_t end_obj = as_text.find('}');
  size_t end = std::min(
      end_arr == std::string::npos ? as_text.size() : end_arr + 1,
      end_obj == std::string::npos ? as_text.size() : end_obj + 1);
  as_text = as_text.substr(0, end);

  Json::CharReaderBuilder b;
  auto reader = std::unique_ptr<Json::CharReader>(b.newCharReader());
  Json::Value parsed;
  std::string errs;
  bool ok = reader->parse(as_text.data(),
                          as_text.data() + as_text.size(),
                          &parsed, &errs);
  if (!ok) {
    std::fprintf(stderr, "json parse failed: %s\n", errs.c_str());
    return 0;
  }
  std::fprintf(stderr, "parsed ok, type=%d (array=%d object=%d)\n",
               (int)parsed.type(),
               (int)Json::arrayValue, (int)Json::objectValue);

  // The same shape AudioConstraints::TryParse uses: bind const Value&,
  // index with a string key. On an arrayValue root this aborts.
  const Json::Value& root = parsed;
  static const std::string_view kMaxSampleRate = "maxSampleRate";
  const Json::Value& v = root[std::string(kMaxSampleRate)];  // <-- aborts here
  std::fprintf(stderr, "lookup returned type=%d\n", (int)v.type());
  return 0;
}
