*** Settings ***
Library    SSHLibrary
Resource    api.resource

*** Keywords ***
Retry test
    [Arguments]    ${keyword}
    Wait Until Keyword Succeeds    90 seconds    3 seconds    ${keyword}

MCP live endpoint answers
    ${rc} =    Execute Command    curl -sf ${backend_url}/health/live
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

*** Test Cases ***
Check if nextcloud-mcp-server is installed correctly
    ${output}  ${rc} =    Execute Command    add-module ${IMAGE_URL} 1
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}  0
    &{output} =    Evaluate    ${output}
    Set Suite Variable    ${module_id}    ${output.module_id}

Check if nextcloud-mcp-server can be configured
    ${rc} =    Execute Command    api-cli run module/${module_id}/configure-module --data '{"host": "mcp.example.com", "http2https": true, "lets_encrypt": false, "nextcloud_host": "https://cloud.example.com", "nextcloud_username": "alice", "nextcloud_password": "app-password", "ollama_base_url": "http://pc01:11434", "ollama_embedding_model": "nomic-embed-text", "enable_semantic_search": true}'
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Check if the MCP service is loaded
    ${output}  ${rc} =    Execute Command    runagent -m ${module_id} systemctl --user show --property=LoadState nextcloud-mcp-server
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}  0
    Should Be Equal As Strings    ${output}    LoadState=loaded

Check if the Qdrant service is loaded
    ${output}  ${rc} =    Execute Command    runagent -m ${module_id} systemctl --user show --property=LoadState nextcloud-mcp-qdrant
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}  0
    Should Be Equal As Strings    ${output}    LoadState=loaded

Retrieve the Traefik backend URL
    ${response} =    Run task     module/traefik1/get-route    {"instance":"${module_id}"}
    Set Suite Variable    ${backend_url}    ${response['url']}

Check if the MCP server process answers
    Retry test    MCP live endpoint answers

Check if nextcloud-mcp-server is removed correctly
    ${rc} =    Execute Command    remove-module --no-preserve ${module_id}
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0
