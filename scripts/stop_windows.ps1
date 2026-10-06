# Stop FinAlly (Windows PowerShell). The data volume is kept.
$Container = "finally"
$exists = docker ps -a --format "{{.Names}}" | Where-Object { $_ -eq $Container }
if ($exists) {
    docker rm -f $Container | Out-Null
    Write-Host "FinAlly stopped. Data persists in the 'finally-data' volume."
} else {
    Write-Host "FinAlly is not running."
}
