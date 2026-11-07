# Read-only checks compatible with Windows 7's PowerShell 2.0.
param(
    [string]$ApiUrl = 'https://api.sendhelp.us/',
    [string]$MeshUrl = 'https://mesh.sendhelp.us/'
)
$ErrorActionPreference = 'Continue'
$os = Get-WmiObject Win32_OperatingSystem
Write-Output ('Windows: {0}; version {1}; SP {2}; {3}' -f $os.Caption, $os.Version, $os.ServicePackMajorVersion, $os.OSArchitecture)
if ($os.Version -like '6.1.*' -and $os.ServicePackMajorVersion -lt 1) {
    Write-Output 'FAIL: Windows 7 Service Pack 1 is required.'
}
Get-WmiObject Win32_Service -Filter "Name='JustConnect Support' OR Name='Mesh Agent' OR Name='tacticalrmm'" |
    Select-Object Name, State, StartMode | Format-Table -AutoSize
foreach ($address in @($ApiUrl, $MeshUrl)) {
    try {
        $uri = New-Object System.Uri -ArgumentList $address
        if ($uri.Scheme -ne 'https') { throw 'HTTPS is required.' }
        $ips = [Net.Dns]::GetHostAddresses($uri.DnsSafeHost)
        Write-Output ('DNS {0}: {1}' -f $uri.DnsSafeHost, (($ips | ForEach-Object { $_.IPAddressToString }) -join ', '))
        $tcp = New-Object Net.Sockets.TcpClient
        try {
            $pending = $tcp.BeginConnect($uri.DnsSafeHost, $uri.Port, $null, $null)
            if (-not $pending.AsyncWaitHandle.WaitOne(5000, $false)) { throw 'TCP 443 timed out.' }
            $tcp.EndConnect($pending)
            Write-Output ('TCP {0}:{1}: reachable' -f $uri.DnsSafeHost, $uri.Port)
        } finally { $tcp.Close() }
        $http = New-Object -ComObject WinHttp.WinHttpRequest.5.1
        $http.SetTimeouts(5000, 5000, 10000, 10000)
        $http.Option(9) = 2048 # TLS 1.2, matching the MSI; certificate checks stay enabled.
        $http.Open('HEAD', $uri.AbsoluteUri, $false)
        $http.Send()
        Write-Output ('HTTPS TLS 1.2 {0}: HTTP {1}; final address {2}' -f $uri.DnsSafeHost, $http.Status, $http.Option(1))
        if ([int]$http.Status -ge 400) { Write-Output 'CHECK: HTTPS responded, but access was refused or the endpoint returned an error.' }
    } catch {
        Write-Output ('FAIL {0}: {1}' -f $address, $_.Exception.Message)
    }
}
Write-Output 'A running service and reachable HTTPS do not prove remote desktop works. Confirm the PC is online in Support and test a desktop session.'
