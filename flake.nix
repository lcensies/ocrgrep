{
  description = "Parallel OCR grep over images";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = nixpkgs.lib.genAttrs systems;
    in {
      packages = forAll (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          tsgrep = pkgs.python3Packages.buildPythonApplication {
            pname = "tsgrep";
            version = "0.1.0";
            src = ./.;
            format = "pyproject";
            nativeBuildInputs = [ pkgs.python3Packages.hatchling ];
            buildInputs = [ pkgs.tesseract5 pkgs.leptonica ];
            propagatedBuildInputs = with pkgs.python3Packages; [
              pillow
              tqdm
              tesserocr
            ];
            meta = {
              description = "Parallel OCR grep over images";
              license = pkgs.lib.licenses.mit;
              mainProgram = "tsgrep";
            };
          };
        in {
          default = tsgrep;
          tsgrep = tsgrep;
        });

      apps = forAll (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/tsgrep";
        };
      });

      devShells = forAll (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in {
          default = pkgs.mkShell {
            buildInputs = [
              pkgs.tesseract5
              pkgs.leptonica
              pkgs.libtesseract
              (pkgs.python3.withPackages (p: [ p.pillow p.tqdm ]))
            ];
          };
        });
    };
}
