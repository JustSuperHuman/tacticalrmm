// Exercises the native actions without installing a product or starting an agent.
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <msi.h>
#include <msiquery.h>
#include <string>
#include <vector>
#include <cstdio>

using std::wstring;
using Action = UINT (__stdcall *)(MSIHANDLE);
static bool cancel = false;
static int CALLBACK ui(void*, UINT type, LPCWSTR) {
    return cancel && (type & 0xff000000) == INSTALLMESSAGE_PROGRESS ? IDCANCEL : 0;
}
static wstring get(MSIHANDLE session, const wchar_t* name) {
    DWORD length = 0; wchar_t empty[1] = {0};
    MsiGetPropertyW(session, name, empty, &length);
    std::vector<wchar_t> buffer(length + 1); ++length;
    MsiGetPropertyW(session, name, buffer.data(), &length);
    return buffer.data();
}
static void check(bool ok, const char* reason) {
    if (!ok) { std::fprintf(stderr, "FAIL: %s\n", reason); ExitProcess(1); }
}
static bool exists(const wstring& file) { return GetFileAttributesW(file.c_str()) != INVALID_FILE_ATTRIBUTES; }
int wmain(int argc, wchar_t** argv) {
    check(argc == 4, "pass MSI, action DLL and an empty workspace test directory");
    wstring directory = argv[3];
    check(CreateDirectoryW(directory.c_str(), nullptr) != FALSE, "test directory must be new");
    directory += L"\\";
    MsiSetInternalUI(INSTALLUILEVEL_NONE, nullptr);
    MsiSetExternalUIW(ui, INSTALLLOGMODE_PROGRESS, nullptr);
    MSIHANDLE session = 0;
    UINT opened = MsiOpenPackageExW(argv[1], MSIOPENPACKAGEFLAGS_IGNOREMACHINESTATE, &session);
    if (opened != ERROR_SUCCESS) std::fprintf(stderr, "Windows Installer error: %u\n", opened);
    check(opened == ERROR_SUCCESS, "MSI opens in the Windows Installer engine");
    HMODULE dll = LoadLibraryW(argv[2]);
    check(dll != nullptr, "native x86 action library loads");
    Action install = reinterpret_cast<Action>(GetProcAddress(dll, "InstallClient"));
    Action rollback = reinterpret_cast<Action>(GetProcAddress(dll, "RollbackClient"));
    Action commit = reinterpret_cast<Action>(GetProcAddress(dll, "CommitClient"));
    Action prepare = reinterpret_cast<Action>(GetProcAddress(dll, "PrepareInstall"));
    check(install && rollback && commit, "MSI action exports exist");
    MsiSetPropertyW(session, L"SupportInstallDir", directory.c_str());
    MsiSetPropertyW(session, L"VersionNT", L"601");
    MsiSetPropertyW(session, L"VersionNT64", L"601");
    check(prepare(session) == ERROR_SUCCESS && get(session, L"InstallClient").find(get(session, L"LegacyUrl")) != wstring::npos, "Windows 7 automatically selects the compatibility client");
    MsiSetPropertyW(session, L"VersionNT", L"1000");
    MsiSetPropertyW(session, L"VersionNT64", L"");
    check(prepare(session) == ERROR_SUCCESS && get(session, L"InstallClient").find(get(session, L"LegacyUrl")) != wstring::npos, "32-bit Windows automatically selects the x86 client");
    MsiSetPropertyW(session, L"VersionNT64", L"1000");
    check(prepare(session) == ERROR_SUCCESS && get(session, L"InstallClient").find(get(session, L"ModernUrl")) != wstring::npos, "modern x64 Windows automatically selects the x64 client");
    auto configure = [&](const wstring& digest) {
        wstring data = L"support|" + directory + L"|" + get(session, L"LegacyUrl") + L"|" + digest + L"|||0";
        check(MsiSetPropertyW(session, L"CustomActionData", data.c_str()) == ERROR_SUCCESS, "action data set");
    };
    wstring agent = directory + L"MeshAgent.exe";
    configure(wstring(64, L'0'));
    check(install(session) == ERROR_INSTALL_FAILURE, "a bad digest prevents installation");
    check(!exists(agent) && !exists(directory + L"client.download"), "failed download leaves no client");
    configure(get(session, L"LegacySha256"));
    cancel = true;
    check(install(session) == ERROR_INSTALL_USEREXIT, "cancellation is honored");
    cancel = false;
    check(!exists(agent) && !exists(directory + L"client.download"), "cancelled download is removed");
    check(install(session) == ERROR_SUCCESS, "verified HTTPS download installs the client");
    check(exists(agent) && exists(directory + L"setup.pending"), "client stays pending until commit");
    check(rollback(session) == ERROR_SUCCESS && !exists(agent), "rollback removes the new client");
    // A repair/upgrade must restore the old binary if the transaction rolls back.
    HANDLE old = CreateFileW(agent.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, 0, nullptr);
    DWORD written = 0; WriteFile(old, "old", 3, &written, nullptr); CloseHandle(old);
    check(install(session) == ERROR_SUCCESS, "existing client can be replaced transactionally");
    check(rollback(session) == ERROR_SUCCESS, "existing client rolls back");
    HANDLE restored = CreateFileW(agent.c_str(), GENERIC_READ, 0, nullptr, OPEN_EXISTING, 0, nullptr);
    check(restored != INVALID_HANDLE_VALUE && GetFileSize(restored, nullptr) == 3, "the original client is restored");
    CloseHandle(restored);
    check(install(session) == ERROR_SUCCESS && commit(session) == ERROR_SUCCESS, "verified installation commits");
    check(!exists(directory + L"client.previous") && !exists(directory + L"setup.pending"), "commit removes staging files");
    DeleteFileW(agent.c_str());
    RemoveDirectoryW(directory.c_str());
    MsiCloseHandle(session); FreeLibrary(dll);
    std::puts("Native MSI actions: verified download, tampering rejection, cancellation, rollback, repair and commit passed. No agent was started.");
    return 0;
}
