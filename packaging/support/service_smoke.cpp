// Exercises production service actions with an in-memory SCM fixture.
// Downloads the real pinned client, but never executes it or creates a real service.
#define UNICODE
#define _UNICODE
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <string>
#include <vector>
#include <cstdio>

static bool present = false, allow_install = true, running = false;
static std::wstring service_image;
static int installs = 0, deletes = 0, foreign_calls = 0;
static int fail_after_running_checks = -1;
static DWORD start_type = SERVICE_AUTO_START;
static SC_HANDLE WINAPI mock_manager(LPCWSTR, LPCWSTR, DWORD) { return reinterpret_cast<SC_HANDLE>(1); }
static BOOL WINAPI mock_close(SC_HANDLE) { return TRUE; }
static SC_HANDLE WINAPI mock_open(SC_HANDLE, LPCWSTR name, DWORD) {
    if (wcscmp(name, L"JustConnect Support") != 0) { ++foreign_calls; SetLastError(ERROR_SERVICE_DOES_NOT_EXIST); return nullptr; }
    if (!present) { SetLastError(ERROR_SERVICE_DOES_NOT_EXIST); return nullptr; }
    return reinterpret_cast<SC_HANDLE>(2);
}
static BOOL WINAPI mock_query_config(SC_HANDLE, LPQUERY_SERVICE_CONFIGW value, DWORD size, LPDWORD needed) {
    *needed = sizeof(QUERY_SERVICE_CONFIGW);
    if (size < *needed) { SetLastError(ERROR_INSUFFICIENT_BUFFER); return FALSE; }
    ZeroMemory(value, sizeof(*value)); value->dwServiceType = SERVICE_WIN32_OWN_PROCESS;
    value->lpBinaryPathName = &service_image[0]; value->dwStartType = start_type;
    return TRUE;
}
static BOOL WINAPI mock_status(SC_HANDLE, LPSERVICE_STATUS state) {
    if (running && fail_after_running_checks >= 0 && fail_after_running_checks-- == 0) running = false;
    ZeroMemory(state, sizeof(*state)); state->dwCurrentState = running ? SERVICE_RUNNING : SERVICE_STOPPED; return TRUE;
}
static BOOL WINAPI mock_control(SC_HANDLE, DWORD control, LPSERVICE_STATUS state) {
    if (control != SERVICE_CONTROL_STOP) return FALSE;
    running = false; return mock_status(nullptr, state);
}
static BOOL WINAPI mock_delete(SC_HANDLE) { present = running = false; ++deletes; return TRUE; }
static BOOL WINAPI mock_change(SC_HANDLE, DWORD, DWORD type, DWORD, LPCWSTR, LPCWSTR, LPDWORD, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR) {
    start_type = type; return TRUE;
}
static BOOL WINAPI mock_start(SC_HANDLE, DWORD, LPCWSTR*) { running = true; return TRUE; }
static SC_HANDLE WINAPI mock_create(SC_HANDLE, LPCWSTR name, LPCWSTR, DWORD, DWORD type, DWORD start,
        DWORD, LPCWSTR command, LPCWSTR, LPDWORD, LPCWSTR, LPCWSTR, LPCWSTR) {
    if (wcscmp(name, L"JustConnect Support") || type != SERVICE_WIN32_OWN_PROCESS ||
        start != SERVICE_AUTO_START || !wcsstr(command, L"--meshServiceName=\"JustConnect Support\" --readmsh=1")) return nullptr;
    ++installs;
    if (allow_install) { present = true; running = false; service_image = command; }
    return reinterpret_cast<SC_HANDLE>(2);
}
static BOOL WINAPI mock_change2(SC_HANDLE, DWORD, LPVOID) { return TRUE; }
#define OpenSCManagerW mock_manager
#define CloseServiceHandle mock_close
#define OpenServiceW mock_open
#define QueryServiceConfigW mock_query_config
#define QueryServiceStatus mock_status
#define ControlService mock_control
#define DeleteService mock_delete
#define ChangeServiceConfigW mock_change
#define StartServiceW mock_start
#define CreateServiceW mock_create
#define ChangeServiceConfig2W mock_change2
#include "../../api/tacticalrmm/support/installer/actions.cpp"

static void check(bool ok, const char* reason) {
    if (!ok) { std::fprintf(stderr, "FAIL: %s\n", reason); ExitProcess(1); }
}
int wmain(int argc, wchar_t** argv) {
    check(argc == 3, "pass the generated MSI and a new test directory");
    std::wstring directory = argv[2];
    check(CreateDirectoryW(directory.c_str(), nullptr) != FALSE, "test directory must be new");
    directory += L"\\";
    MsiSetInternalUI(INSTALLUILEVEL_NONE, nullptr);
    MSIHANDLE session = 0;
    check(MsiOpenPackageExW(argv[1], MSIOPENPACKAGEFLAGS_IGNOREMACHINESTATE, &session) == ERROR_SUCCESS, "MSI opens");
    auto configure = [&](bool repair) {
        std::wstring data = L"support-service|" + directory + L"|" + property(session, L"LegacyUrl") + L"|" +
            property(session, L"LegacySha256") + L"|||" + (repair ? L"1" : L"0");
        MsiSetPropertyW(session, L"CustomActionData", data.c_str());
    };
    configure(false);
    check(InstallClient(session) == ERROR_SUCCESS && present && running && installs == 1, "fresh setup creates and starts its service");
    check(CommitClient(session) == ERROR_SUCCESS, "fresh install commits");
    std::wstring database = directory + L"MeshAgent.db";
    write_file(database, {'k', 'e', 'y'});
    configure(true); start_type = SERVICE_DEMAND_START;
    check(InstallClient(session) == ERROR_SUCCESS && running && installs == 1 && start_type == SERVICE_AUTO_START, "repair reuses its service and restores automatic start");
    check(RollbackClient(session) == ERROR_SUCCESS && present && running && deletes == 0, "repair rollback restores the service and executable");
    check(exists(database), "repair preserves agent identity");
    std::wstring upgrade = property(session, L"CustomActionData") + L"|1";
    MsiSetPropertyW(session, L"CustomActionData", upgrade.c_str());
    check(UninstallClient(session) == ERROR_SUCCESS && !present && deletes == 1, "uninstall removes only its service");
    check(exists(database), "an MSI upgrade preserves agent identity");
    DeleteFileW((directory + L"MeshAgent.exe").c_str());
    configure(false); allow_install = false;
    check(InstallClient(session) == ERROR_INSTALL_FAILURE && !present, "an absent service after registration is rejected");
    check(RollbackClient(session) == ERROR_SUCCESS && !exists(directory + L"MeshAgent.exe"), "failed fresh install rolls back the downloaded executable");
    present = running = true; service_image = L"\"C:\\OtherProduct\\MeshAgent.exe\"";
    check(InstallClient(session) == ERROR_INSTALL_FAILURE && present && running, "a conflicting service is not replaced");
    check(UninstallClient(session) == ERROR_INSTALL_FAILURE && present && running && deletes == 1, "a conflicting service is not removed");
    service_image = L"\"" + directory + L"MeshAgent.exe\"";
    check(UninstallClient(session) == ERROR_SUCCESS && !present && !exists(database), "normal uninstall removes its agent identity");
    allow_install = true; fail_after_running_checks = 4;
    check(InstallClient(session) == ERROR_INSTALL_FAILURE, "an agent that exits just after reporting running is rejected");
    check(RollbackClient(session) == ERROR_SUCCESS && !present, "early service failure is rolled back");
    check(foreign_calls == 0, "monitoring and other service names are never accessed");
    DeleteFileW(database.c_str()); RemoveDirectoryW(directory.c_str());
    MsiCloseHandle(session);
    std::puts("Service actions: install/start verification, repair, automatic start, rollback, identity preservation and conflicting-service protection passed. No agent was executed and no real service was created.");
    return 0;
}
