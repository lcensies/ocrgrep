{ lib, python3Packages, tesseract5, leptonica }:

python3Packages.buildPythonApplication {
  pname = "tsgrep";
  version = "0.1.0";
  src = ../.;

  format = "pyproject";

  nativeBuildInputs = [ python3Packages.hatchling ];

  buildInputs = [ tesseract5 leptonica ];

  propagatedBuildInputs = with python3Packages; [
    pillow
    tqdm
    tesserocr
  ];

  meta = {
    description = "Parallel OCR grep over images";
    license = lib.licenses.mit;
    mainProgram = "tsgrep";
  };
}
