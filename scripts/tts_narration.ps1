Add-Type -AssemblyName System.Speech

$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$jsonPath = Join-Path $repo "docs\narration.json"
$outDir = Join-Path $repo "docs\narration"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }

$voice = if ($env:TTS_VOICE) { $env:TTS_VOICE } else { "Microsoft Huihui Desktop" }
$rate = if ($env:TTS_RATE) { [int]$env:TTS_RATE } else { 0 }

$data = Get-Content -Raw -Encoding UTF8 $jsonPath | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice($voice)
$synth.Rate = $rate

foreach ($scene in $data.scenes) {
    $wav = Join-Path $outDir ("s" + $scene.id + ".wav")
    if (Test-Path $wav) { Remove-Item $wav -Force }
    $synth.SetOutputToWaveFile($wav)
    $synth.Speak($scene.text)
    $synth.SetOutputToNull()
    Write-Output ("s" + $scene.id + " -> " + $wav)
}

$synth.Dispose()
Write-Output ("voice=" + $voice + " rate=" + $rate)
