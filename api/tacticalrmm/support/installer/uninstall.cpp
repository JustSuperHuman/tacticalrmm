// Preserve the monitoring agent's existing unins001.exe uninstall contract.
// This GUI-subsystem bridge starts Windows Installer and exits before it is removed.
#define UNICODE
#define _UNICODE
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <string>

int WINAPI wWinMain(HINSTANCE, HINSTANCE, LPWSTR, int) {
    HKEY key = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"Software\\JustConnect\\monitoring", 0,
                     KEY_QUERY_VALUE | KEY_WOW64_32KEY, &key) != ERROR_SUCCESS) return 1;
    wchar_t product[64] = {}; DWORD type = 0, size = sizeof(product);
    LONG result = RegQueryValueExW(key, L"ProductCode", nullptr, &type,
                                   reinterpret_cast<BYTE*>(product), &size);
    RegCloseKey(key);
    if (result != ERROR_SUCCESS || type != REG_SZ || lstrlenW(product) != 38 ||
        product[0] != L'{' || product[37] != L'}') return 1;
    for (int i = 1; i < 37; ++i) {
        wchar_t c = product[i];
        if (!((c >= L'0' && c <= L'9') || (c >= L'A' && c <= L'F') ||
              (c >= L'a' && c <= L'f') || c == L'-')) return 1;
    }
    wchar_t system[MAX_PATH];
    if (!GetSystemDirectoryW(system, MAX_PATH)) return 1;
    std::wstring image = std::wstring(system) + L"\\msiexec.exe";
    std::wstring command = L"\"" + image + L"\" /x " + product + L" /qn /norestart";
    STARTUPINFOW startup = {}; startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW; startup.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION process = {};
    if (!CreateProcessW(image.c_str(), &command[0], nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process)) return 1;
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    return 0;
}
