# Einmaliger, losgeloster Start des Qwen-Image-2.1-Generators (Test).
# Start-Process erzeugt einen eigenstaendigen Windows-Prozess, der ueberlebt,
# wenn die aufrufende Shell/Bash-Session beendet wird.
$Root  = 'C:\Users\user\ai_framework-main'
$Py    = "$Root\z-video\venv\Scripts\python.exe"
$Gen   = "$Root\qwen-image\generate.py"
$Log   = "$Root\qwen-image\gen.log"
$Err   = "$Root\qwen-image\gen.err.log"
$Args_ = @(
  $Gen,
  'a majestic cat sitting on a windowsill, soft golden evening light, detailed fur, cozy atmosphere',
  '--offload','--width','1024','--height','1024','--steps','50',
  '--outdir',  "$Root\qwen-image\outputs",
  '--out','testbild.png'
)
Remove-Item $Log -ErrorAction SilentlyContinue
Remove-Item $Err -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $Py -ArgumentList $Args_ -WindowStyle Hidden -PassThru `
         -RedirectStandardOutput $Log -RedirectStandardError $Err
Write-Output ("GEN_PID=" + $proc.Id)
