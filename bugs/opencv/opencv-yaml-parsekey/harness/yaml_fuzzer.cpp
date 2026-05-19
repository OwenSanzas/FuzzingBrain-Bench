// Driver for opencv-yaml-parsekey: writes the fuzzer input to a tempfile
// and feeds it to cv::FileStorage's YAML parser. Adapted from the
// upstream poc.cpp (PR #28619 issue body).
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <string>
#include <unistd.h>

#include <opencv2/core.hpp>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    if (size > 64 * 1024) return 0;

    char tmpf[] = "/tmp/fb_opencv_yamlXXXXXX.yaml";
    int fd = mkstemps(tmpf, 5);
    if (fd < 0) return 0;
    ssize_t w = write(fd, data, size);
    close(fd);
    if (w < 0 || (size_t)w != size) {
        unlink(tmpf);
        return 0;
    }

    try {
        cv::FileStorage fs(tmpf, cv::FileStorage::READ);
        (void)fs;
    } catch (const cv::Exception&) {
        // Expected for many malformed YAMLs.
    }

    unlink(tmpf);
    return 0;
}
