// Native Windows Installer actions. Target Windows 7 SP1; no CLR or shell needed.
#define UNICODE
#define _UNICODE
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <msi.h>
#include <msiquery.h>
#include <winhttp.h>
#include <bcrypt.h>
#include <tlhelp32.h>
#include <shlwapi.h>
#include <string>
#include <vector>
#include <stdexcept>

using std::wstring;
static const DWORD MaxDownload = 256 * 1024 * 1024;

struct Handle {
    HANDLE value;
    explicit Handle(HANDLE h = INVALID_HANDLE_VALUE) : value(h) {}
    ~Handle() { if (value != INVALID_HANDLE_VALUE && value) CloseHandle(value); }
    operator HANDLE() const { return value; }
};
struct HttpHandle {
    HINTERNET value;
    explicit HttpHandle(HINTERNET h = nullptr) : value(h) {}
    ~HttpHandle() { if (value) WinHttpCloseHandle(value); }
    operator HINTERNET() const { return value; }
};
struct Cancelled {};
struct Failure { const wchar_t* message; };

static void require(bool ok, const wchar_t* message) {
    if (!ok) throw Failure{message};
}
static wstring property(MSIHANDLE session, const wchar_t* name) {
    DWORD length = 0;
    wchar_t empty[1] = {0};
    MsiGetPropertyW(session, name, empty, &length);
    std::vector<wchar_t> value(length + 1);
    DWORD capacity = length + 1;
    require(MsiGetPropertyW(session, name, value.data(), &capacity) == ERROR_SUCCESS,
            L"Setup could not read its configuration. Download a new installer.");
    return wstring(value.data());
}
static void message(MSIHANDLE session, INSTALLMESSAGE kind, const wchar_t* text) {
    MSIHANDLE record = MsiCreateRecord(1);
    MsiRecordSetStringW(record, 0, L"[1]");
    MsiRecordSetStringW(record, 1, text);
    int result = MsiProcessMessage(session, kind, record);
    MsiCloseHandle(record);
    if (result == IDCANCEL) throw Cancelled{};
}
static void status(MSIHANDLE session, const wchar_t* text) {
    MSIHANDLE record = MsiCreateRecord(3);
    MsiRecordSetStringW(record, 1, L"InstallClient");
    MsiRecordSetStringW(record, 2, text);
    MsiRecordSetStringW(record, 3, L"[1]");
    int result = MsiProcessMessage(session, INSTALLMESSAGE_ACTIONSTART, record);
    MsiCloseHandle(record);
    if (result == IDCANCEL) throw Cancelled{};
}
static void progress(MSIHANDLE session, int type, int ticks) {
    MSIHANDLE record = MsiCreateRecord(4);
    MsiRecordSetInteger(record, 1, type);
    MsiRecordSetInteger(record, 2, ticks);
    MsiRecordSetInteger(record, 3, 0);
    MsiRecordSetInteger(record, 4, 0);
    int result = MsiProcessMessage(session, INSTALLMESSAGE_PROGRESS, record);
    MsiCloseHandle(record);
    if (result == IDCANCEL) throw Cancelled{};
}
static int nibble(wchar_t c) {
    if (c >= L'0' && c <= L'9') return c - L'0';
    if (c >= L'a' && c <= L'f') return c - L'a' + 10;
    throw Failure{L"Setup's verification information is invalid. Download a new installer."};
}
static std::vector<BYTE> unhex(const wstring& input) {
    require(input.size() % 2 == 0, L"The installer configuration is invalid.");
    std::vector<BYTE> bytes(input.size() / 2);
    for (size_t i = 0; i < bytes.size(); ++i)
        bytes[i] = static_cast<BYTE>((nibble(input[i * 2]) << 4) | nibble(input[i * 2 + 1]));
    return bytes;
}
static wstring utf8(const std::vector<BYTE>& bytes) {
    if (bytes.empty()) return L"";
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
        reinterpret_cast<const char*>(bytes.data()), static_cast<int>(bytes.size()), nullptr, 0);
    require(count > 0, L"The installer configuration is invalid.");
    wstring result(count, 0);
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
        reinterpret_cast<const char*>(bytes.data()), static_cast<int>(bytes.size()), &result[0], count);
    return result;
}
struct Configuration {
    wstring mode, directory, url, digest, settings, arguments;
    bool repair;
    explicit Configuration(MSIHANDLE session) {
        wstring value = property(session, L"CustomActionData");
        std::vector<wstring> fields;
        size_t start = 0;
        for (size_t i = 0; i <= value.size(); ++i) {
            if (i == value.size() || value[i] == L'|') {
                fields.push_back(value.substr(start, i - start)); start = i + 1;
            }
        }
        require(fields.size() == 7, L"Download a new copy of this installer from your technician.");
        mode = fields[0]; directory = fields[1]; url = fields[2]; digest = fields[3];
        settings = fields[4]; arguments = fields[5]; repair = fields[6] == L"1";
        require(mode == L"support" || mode == L"monitoring", L"Invalid installer type.");
        require(directory.size() > 8 && directory.size() < MAX_PATH - 60,
                L"The installation folder is not available.");
        if (directory.back() != L'\\') directory += L'\\';
        // Every file operation below names a product file; there is no recursive deletion.
    }
    wstring file(const wchar_t* name) const { return directory + name; }
    wstring agent() const { return file(mode == L"support" ? L"MeshAgent.exe" : L"tacticalrmm.exe"); }
};
static bool exists(const wstring& path) {
    return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES;
}
static void write_file(const wstring& path, const std::vector<BYTE>& bytes) {
    Handle file(CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr));
    DWORD written = 0;
    require(file != INVALID_HANDLE_VALUE && (bytes.empty() || (WriteFile(file, bytes.data(),
            static_cast<DWORD>(bytes.size()), &written, nullptr) && written == bytes.size())),
            L"Setup could not save the support configuration. Check available disk space.");
}
static void stop_process(const wstring& image) {
    Handle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0));
    if (snapshot == INVALID_HANDLE_VALUE) return;
    PROCESSENTRY32W entry = {}; entry.dwSize = sizeof(entry);
    if (!Process32FirstW(snapshot, &entry)) return;
    do {
        Handle process(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE | SYNCHRONIZE,
                                   FALSE, entry.th32ProcessID));
        if (process == INVALID_HANDLE_VALUE || !process) continue;
        wchar_t name[32768]; DWORD length = 32768;
        if (QueryFullProcessImageNameW(process, 0, name, &length) &&
            lstrcmpiW(name, image.c_str()) == 0) {
            TerminateProcess(process, 0); WaitForSingleObject(process, 5000);
        }
    } while (Process32NextW(snapshot, &entry));
}
static bool service_exists() {
    SC_HANDLE manager = OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT);
    if (!manager) return false;
    SC_HANDLE service = OpenServiceW(manager, L"tacticalrmm", SERVICE_QUERY_STATUS);
    bool present = service != nullptr;
    if (service) CloseServiceHandle(service);
    CloseServiceHandle(manager);
    return present;
}
static bool enrolled() {
    for (REGSAM view : {KEY_WOW64_32KEY, KEY_WOW64_64KEY}) {
        HKEY key = nullptr;
        if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\TacticalRMM", 0, KEY_QUERY_VALUE | view, &key) != ERROR_SUCCESS) continue;
        wchar_t value[256]; DWORD size = sizeof(value), type = 0;
        bool valid = RegQueryValueExW(key, L"AgentID", nullptr, &type, reinterpret_cast<BYTE*>(value), &size) == ERROR_SUCCESS && type == REG_SZ && size > sizeof(wchar_t);
        RegCloseKey(key);
        if (valid) return true;
    }
    return false;
}
static void remove_service() {
    SC_HANDLE manager = OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT);
    if (!manager) return;
    SC_HANDLE service = OpenServiceW(manager, L"tacticalrmm", SERVICE_STOP | DELETE | SERVICE_QUERY_STATUS);
    if (service) {
        SERVICE_STATUS state = {};
        ControlService(service, SERVICE_CONTROL_STOP, &state);
        for (int i = 0; i < 50; ++i) {
            if (!QueryServiceStatus(service, &state) || state.dwCurrentState == SERVICE_STOPPED) break;
            Sleep(100);
        }
        DeleteService(service); CloseServiceHandle(service);
    }
    CloseServiceHandle(manager);
}
static DWORD run_hidden(MSIHANDLE session, const wstring& image, const wstring& arguments,
                        DWORD timeout = 15 * 60 * 1000, const wstring& program_files = L"") {
    wstring command = L"\"" + image + L"\" " + arguments;
    STARTUPINFOW startup = {}; startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW; startup.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION info = {};
    // The x86 compatibility payload on x64 Windows must use the same directory
    // as the native x64 payload and the upstream service's executable path.
    std::vector<wchar_t> environment;
    if (!program_files.empty()) {
        LPWCH block = GetEnvironmentStringsW();
        if (block) {
            for (const wchar_t* entry = block; *entry; entry += lstrlenW(entry) + 1) {
                if (_wcsnicmp(entry, L"ProgramFiles=", 13) != 0)
                    environment.insert(environment.end(), entry, entry + lstrlenW(entry) + 1);
            }
            FreeEnvironmentStringsW(block);
        }
        wstring value = L"ProgramFiles=" + program_files;
        environment.insert(environment.end(), value.begin(), value.end());
        environment.push_back(0); environment.push_back(0);
    }
    require(CreateProcessW(image.c_str(), &command[0], nullptr, nullptr, FALSE,
            CREATE_NO_WINDOW | (environment.empty() ? 0 : CREATE_UNICODE_ENVIRONMENT),
            environment.empty() ? nullptr : environment.data(), nullptr, &startup, &info) != FALSE,
            L"Setup could not start the client. Contact your technician.");
    Handle process(info.hProcess), thread(info.hThread);
    DWORD elapsed = 0;
    while (WaitForSingleObject(process, 200) == WAIT_TIMEOUT) {
        try { progress(session, 2, 0); }
        catch (...) { TerminateProcess(process, ERROR_INSTALL_USEREXIT); throw; }
        elapsed += 200;
        if (elapsed >= timeout) {
            TerminateProcess(process, ERROR_TIMEOUT);
            throw Failure{L"The client did not finish setting up. Check your connection and try again."};
        }
    }
    DWORD exit = ERROR_INSTALL_FAILURE;
    GetExitCodeProcess(process, &exit);
    return exit;
}
static void download(MSIHANDLE session, wstring url, const wstring& destination,
                     const wstring& expected_hex) {
    std::vector<BYTE> expected = unhex(expected_hex);
    require(expected.size() == 32, L"Setup's download verification is missing.");
    HttpHandle internet(WinHttpOpen(L"JustConnect Setup/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                   WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0));
    require(internet.value != nullptr, L"Windows could not start a secure download.");
    DWORD tls = WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2;
    require(WinHttpSetOption(internet, WINHTTP_OPTION_SECURE_PROTOCOLS, &tls, sizeof(tls)) != FALSE,
            L"Windows needs TLS 1.2 support before setup can continue. Contact your technician.");
    WinHttpSetTimeouts(internet, 15000, 15000, 30000, 30000);
    for (int redirects = 0; redirects <= 5; ++redirects) {
        URL_COMPONENTSW parts = {}; parts.dwStructSize = sizeof(parts);
        parts.dwHostNameLength = parts.dwUrlPathLength = parts.dwExtraInfoLength =
            parts.dwUserNameLength = parts.dwPasswordLength = static_cast<DWORD>(-1);
        require(WinHttpCrackUrl(url.c_str(), static_cast<DWORD>(url.size()), 0, &parts) &&
                parts.nScheme == INTERNET_SCHEME_HTTPS && !parts.dwUserNameLength && !parts.dwPasswordLength,
                L"Setup requires a secure HTTPS download address.");
        wstring host(parts.lpszHostName, parts.dwHostNameLength);
        wstring target(parts.lpszUrlPath, parts.dwUrlPathLength);
        if (parts.dwExtraInfoLength) target.append(parts.lpszExtraInfo, parts.dwExtraInfoLength);
        if (target.empty()) target = L"/";
        HttpHandle connection(WinHttpConnect(internet, host.c_str(), parts.nPort, 0));
        HttpHandle request(WinHttpOpenRequest(connection, L"GET", target.c_str(), nullptr,
                WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE));
        DWORD redirect_policy = WINHTTP_OPTION_REDIRECT_POLICY_NEVER;
        if (request.value) WinHttpSetOption(request, WINHTTP_OPTION_REDIRECT_POLICY, &redirect_policy, sizeof(redirect_policy));
        require(request.value && WinHttpSendRequest(request, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                WINHTTP_NO_REQUEST_DATA, 0, 0, 0) && WinHttpReceiveResponse(request, nullptr),
                L"Setup could not download the client securely. Check your internet connection and Windows certificate updates, then try again.");
        DWORD code = 0, length = sizeof(code);
        require(WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                WINHTTP_HEADER_NAME_BY_INDEX, &code, &length, WINHTTP_NO_HEADER_INDEX) != FALSE,
                L"The download server did not return a usable response.");
        if (code == 301 || code == 302 || code == 303 || code == 307 || code == 308) {
            wchar_t location[8192]; length = sizeof(location);
            require(WinHttpQueryHeaders(request, WINHTTP_QUERY_LOCATION, WINHTTP_HEADER_NAME_BY_INDEX,
                    location, &length, WINHTTP_NO_HEADER_INDEX) != FALSE,
                    L"The download server returned an invalid redirect.");
            wchar_t combined[8192]; DWORD capacity = 8192;
            require(UrlCombineW(url.c_str(), location, combined, &capacity, 0) == S_OK,
                    L"The download server returned an invalid redirect.");
            url = combined; continue;
        }
        require(code == 200, L"The client download is unavailable. Ask your technician for a newly generated installer.");
        BCRYPT_ALG_HANDLE algorithm = nullptr; BCRYPT_HASH_HANDLE hash = nullptr;
        DWORD object_size = 0, returned = 0;
        require(BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) == 0,
                L"Windows could not verify the download.");
        BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH, reinterpret_cast<PUCHAR>(&object_size),
                          sizeof(object_size), &returned, 0);
        std::vector<BYTE> object(object_size);
        NTSTATUS created = BCryptCreateHash(algorithm, &hash, object.data(), object_size, nullptr, 0, 0);
        if (created != 0) { BCryptCloseAlgorithmProvider(algorithm, 0); throw Failure{L"Windows could not verify the download."}; }
        try {
            Handle file(CreateFileW(destination.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                                   FILE_ATTRIBUTE_NORMAL, nullptr));
            require(file != INVALID_HANDLE_VALUE, L"Setup could not save the client. Check available disk space.");
            std::vector<BYTE> buffer(64 * 1024); DWORD total = 0;
            progress(session, 3, 4096);
            while (true) {
                DWORD read = 0, written = 0;
                require(WinHttpReadData(request, buffer.data(), static_cast<DWORD>(buffer.size()), &read) != FALSE,
                        L"The download was interrupted. Check your connection and run setup again.");
                if (!read) break;
                require(total <= MaxDownload - read, L"The download is larger than expected. Ask your technician for a new installer.");
                total += read;
                require(BCryptHashData(hash, buffer.data(), read, 0) == 0 &&
                        WriteFile(file, buffer.data(), read, &written, nullptr) && written == read,
                        L"Setup could not save or verify the client.");
                progress(session, 2, static_cast<int>(read / 1024));
            }
            BYTE actual[32];
            require(total > 0 && BCryptFinishHash(hash, actual, sizeof(actual), 0) == 0,
                    L"The client download was incomplete. Run setup again.");
            BYTE difference = 0;
            for (size_t i = 0; i < expected.size(); ++i) difference |= actual[i] ^ expected[i];
            require(difference == 0, L"The downloaded client did not match this installer. Nothing was run. Ask your technician for a newly generated installer.");
        } catch (...) {
            BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(algorithm, 0); throw;
        }
        BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(algorithm, 0);
        return;
    }
    throw Failure{L"The download server redirected setup too many times. Contact your technician."};
}
template<class Function> static UINT action(MSIHANDLE session, Function function) {
    try { function(); return ERROR_SUCCESS; }
    catch (const Cancelled&) { return ERROR_INSTALL_USEREXIT; }
    catch (const Failure& error) {
        try { message(session, INSTALLMESSAGE_ERROR, error.message); } catch (...) {}
        return ERROR_INSTALL_FAILURE;
    } catch (...) {
        try { message(session, INSTALLMESSAGE_ERROR, L"Setup could not finish. Download a new installer or contact your technician."); } catch (...) {}
        return ERROR_INSTALL_FAILURE;
    }
}

extern "C" __declspec(dllexport) UINT __stdcall PrepareInstall(MSIHANDLE session) {
    return action(session, [&] {
        bool legacy = property(session, L"VersionNT") == L"601" || property(session, L"VersionNT64").empty();
        wstring mode = property(session, L"PackageMode");
        if (mode == L"monitoring" && property(session, L"Installed").empty())
            require(!service_exists(), L"This computer is already monitored. Use Access to update its existing agent.");
        wstring data = mode + L"|" + property(session, L"SupportInstallDir") + L"|" +
            property(session, legacy ? L"LegacyUrl" : L"ModernUrl") + L"|" +
            property(session, legacy ? L"LegacySha256" : L"ModernSha256") + L"|" +
            property(session, L"MeshSettingsHex") + L"|" + property(session, L"InstallArgumentsHex") +
            L"|" + (property(session, L"Installed").empty() ? L"0" : L"1");
        for (auto name : {L"InstallClient", L"RollbackClient", L"CommitClient", L"UninstallClient", L"StopClient", L"LaunchClient"})
            require(MsiSetPropertyW(session, name, data.c_str()) == ERROR_SUCCESS, L"Setup could not prepare its configuration.");
    });
}
extern "C" __declspec(dllexport) UINT __stdcall StopClient(MSIHANDLE session) {
    return action(session, [&] {
        Configuration config(session);
        if (config.mode == L"support") stop_process(config.agent());
    });
}
extern "C" __declspec(dllexport) UINT __stdcall InstallClient(MSIHANDLE session) {
    return action(session, [&] {
        Configuration config(session);
        if (config.mode == L"monitoring" && config.repair && exists(config.agent()) && service_exists()) return;
        status(session, L"Downloading the right client for this computer…");
        wstring pending = config.file(L"client.download");
        try { download(session, config.url, pending, config.digest); }
        catch (...) { DeleteFileW(pending.c_str()); throw; }
        // Preserve existing files until Windows Installer commits the transaction.
        if (config.mode == L"support" && exists(config.agent())) require(MoveFileExW(config.agent().c_str(), config.file(L"client.previous").c_str(), MOVEFILE_REPLACE_EXISTING), L"Close the support app and try setup again.");
        write_file(config.file(L"setup.pending"), {});
        if (config.mode == L"support") {
            require(MoveFileExW(pending.c_str(), config.agent().c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH), L"Setup could not put the client in its installation folder.");
        } else {
            wstring setup = config.file(L"client-setup.exe");
            require(MoveFileExW(pending.c_str(), setup.c_str(), MOVEFILE_REPLACE_EXISTING), L"Setup could not prepare the monitoring client.");
            wstring directory = config.directory.substr(0, config.directory.size() - 1);
            wstring program_files = directory.substr(0, directory.find_last_of(L'\\'));
            status(session, L"Installing the monitoring client…");
            DWORD result = run_hidden(session, setup, L"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=\"" + directory + L"\"", 15 * 60 * 1000, program_files);
            DeleteFileW(setup.c_str());
            require((result == 0 || result == 3010) && exists(config.agent()), L"The monitoring client could not be installed. Contact your technician.");
            if (config.repair) return;
            status(session, L"Connecting this computer to your technician’s monitoring service…");
            wstring arguments = utf8(unhex(config.arguments)) + L" --silent --meshdir \"" + config.directory + L"mesh\"";
            require(run_hidden(session, config.agent(), arguments, 15 * 60 * 1000, program_files) == 0 && service_exists() && enrolled(),
                    L"The monitoring agent could not finish enrolling. Ask your technician to generate a fresh installer.");
        }
        status(session, L"Finishing setup…");
    });
}
static void cleanup_monitoring(MSIHANDLE session, const Configuration& config) {
    wstring upstream = config.file(L"unins000.exe");
    if (exists(upstream)) {
        run_hidden(session, upstream, L"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART", 120000);
        return;
    }
    remove_service(); stop_process(config.agent());
    if (exists(config.agent())) run_hidden(session, config.agent(), L"-m cleanup", 60000);
    // The monitoring installer sets --meshdir to its own private subdirectory.
    wstring mesh = config.file(L"mesh\\meshagent.exe");
    if (exists(mesh)) run_hidden(session, mesh, L"-fulluninstall", 60000);
}
extern "C" __declspec(dllexport) UINT __stdcall RollbackClient(MSIHANDLE session) {
    return action(session, [&] {
        Configuration config(session);
        if (exists(config.file(L"setup.pending"))) {
            if (config.mode == L"monitoring" && !config.repair) cleanup_monitoring(session, config);
            DeleteFileW(config.agent().c_str());
        }
        if (exists(config.file(L"client.previous"))) MoveFileExW(config.file(L"client.previous").c_str(), config.agent().c_str(), MOVEFILE_REPLACE_EXISTING);
        DeleteFileW(config.file(L"client.download").c_str());
        DeleteFileW(config.file(L"setup.pending").c_str());
    });
}
extern "C" __declspec(dllexport) UINT __stdcall CommitClient(MSIHANDLE session) {
    return action(session, [&] {
        Configuration config(session);
        for (auto name : {L"client.previous", L"settings.previous", L"setup.pending", L"client.download", L"client-setup.exe"}) DeleteFileW(config.file(name).c_str());
    });
}
extern "C" __declspec(dllexport) UINT __stdcall UninstallClient(MSIHANDLE session) {
    return action(session, [&] {
        Configuration config(session);
        if (config.mode == L"monitoring") cleanup_monitoring(session, config);
        else stop_process(config.agent());
    });
}
extern "C" __declspec(dllexport) UINT __stdcall LaunchClient(MSIHANDLE session) {
    return action(session, [&] {
        // Immediate UI action: runs as the person who chose Finish, in their session.
        wstring image = property(session, L"SupportInstallDir") + L"MeshAgent.exe";
        wstring command = L"\"" + image + L"\"";
        STARTUPINFOW startup = {}; startup.cb = sizeof(startup);
        PROCESS_INFORMATION process = {};
        require(CreateProcessW(image.c_str(), &command[0], nullptr, nullptr, FALSE, 0,
                nullptr, nullptr, &startup, &process), L"Setup finished. Open JustConnect Support from the Start menu to connect.");
        CloseHandle(process.hThread); CloseHandle(process.hProcess);
    });
}
