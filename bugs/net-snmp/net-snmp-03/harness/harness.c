

#include <net-snmp/net-snmp-config.h>
#include <net-snmp/net-snmp-includes.h>
#include "../../apps/snmptrapd_handlers.h"
#include "ada_fuzz_header.h"

int LLVMFuzzerInitialize(int *argc, char ***argv) {
    if (getenv("NETSNMP_DEBUGGING") != NULL) {
        
        snmp_enable_stderrlog();
        snmp_set_do_debugging(1);
        debug_register_tokens("");
    }

    return 0;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    oid snmpTrapOid[] = { 1, 3, 6, 1, 6, 3, 1, 1, 4, 1, 0 };
    netsnmp_variable_list var2 = {
        .name = snmpTrapOid,
        .name_length = sizeof(snmpTrapOid) / sizeof(snmpTrapOid[0])
    };
    netsnmp_variable_list var1 = { .next_variable = &var2 };
    netsnmp_transport transport = { };
    netsnmp_session sess = { };
    netsnmp_pdu *pdu;
    int op;

    af_gb_init();
    var2.val_len = af_get_short(&data, &size);
    var2.val.objid = af_gb_get_random_data(&data, &size, var2.val_len);
    if (!var2.val.objid)
        goto cleanup;
    op = NETSNMP_CALLBACK_OP_RECEIVED_MESSAGE;
    pdu = af_gb_get_random_data(&data, &size, sizeof(*pdu));
    if (!pdu)
        goto cleanup;
    pdu->enterprise_length = af_get_short(&data, &size);
    pdu->enterprise = af_gb_get_random_data(&data, &size,
                                            pdu->enterprise_length *
                                            sizeof(pdu->enterprise[0]));
    if (!pdu->enterprise)
        goto cleanup;
    pdu->community = NULL;
    pdu->community_len = 0;
    pdu->contextEngineID = NULL;
    pdu->contextEngineIDLen = 0;
    pdu->securityEngineID = NULL;
    pdu->securityEngineIDLen = 0;
    pdu->contextName = NULL;
    pdu->contextNameLen = 0;
    pdu->securityName = NULL;
    pdu->securityNameLen = 0;
    pdu->transport_data = NULL;
    pdu->transport_data_length = 0;
    pdu->variables = &var1;
    snmp_input(op, &sess, 0, pdu, &transport);

cleanup:
    af_gb_cleanup();

    return 0;
}
