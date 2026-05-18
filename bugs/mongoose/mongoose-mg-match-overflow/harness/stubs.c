/*
 * Stub implementations to satisfy the linker when mongoose is built
 * with MG_ENABLE_SOCKET=0. At commit b313d697 several websocket /
 * DNS / mgr code paths still reference these symbols unconditionally
 * even when sockets are disabled, leaving them unresolved. The bug
 * under test (mg_match pattern matching) does not exercise these
 * code paths, so no-op stubs are sufficient for linking.
 *
 * C linkage matches by symbol name only, so the exact parameter
 * signatures here do not matter for ABI; we just need a definition.
 */
#include <stddef.h>

int mg_send()                { return 1; }
void *mg_connect_resolved()  { return 0; }
int mg_multicast_add()       { return 0; }
int mg_open_listener()       { return -1; }
void mg_mgr_poll()           { }
