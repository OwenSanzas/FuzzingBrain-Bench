#include <json/reader.h>
#include <json/value.h>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>

int main(int argc, char** argv) {
  if (argc != 2) {
    std::fprintf(stderr, "Usage: %s <poc.bin>\n", argv[0]);
    return 1;
  }
  std::ifstream f(argv[1], std::ios::binary);
  std::ostringstream ss;
  ss << f.rdbuf();
  const std::string raw = ss.str();

  // Heap-allocate the exact input size (matches the library call shape).
  char* buf = new char[raw.size()];
  std::memcpy(buf, raw.data(), raw.size());

  Json::CharReaderBuilder builder;
  Json::CharReaderBuilder::strictMode(&builder.settings_);
  std::unique_ptr<Json::CharReader> reader(builder.newCharReader());
  Json::Value root;
  std::string errs;
  // Two-pointer parse — same call shape as
  // openscreen::json::Parse at
  // third_party/openscreen/src/util/json/json_serialization.cc:29.
  reader->parse(buf, buf + raw.size(), &root, &errs);

  delete[] buf;
  return 0;
}
