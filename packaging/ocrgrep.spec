Name:           ocrgrep
Version:        0.1.0
Release:        1%{?dist}
Summary:        Parallel OCR grep over images
License:        MIT
URL:            https://github.com/lcensies/ocrgrep
Source0:        %{name}-%{version}.tar.gz
%global debug_package %{nil}

BuildRequires:  python3-devel
BuildRequires:  tesseract-devel
BuildRequires:  leptonica-devel
Requires:       tesseract
Requires:       python3-pillow
Requires:       python3-tqdm

%description
Runs Tesseract OCR in parallel across a directory tree and prints paths
of images whose text matches a pattern. Supports dedup and checkpointing.

%prep
%autosetup

%build
pip3 wheel --no-deps --wheel-dir dist .

%install
pip3 install --no-deps --no-index --find-links dist \
    --target %{buildroot}%{python3_sitelib} ocrgrep
install -D -m 755 /dev/stdin %{buildroot}/usr/bin/ocrgrep <<'EOF'
#!/bin/sh
exec python3 -c "from ocr_grep import main; main()" "$@"
EOF

%files
/usr/bin/ocrgrep
%{python3_sitelib}/ocr_grep*
%{python3_sitelib}/ocrgrep*

%changelog
* Wed May 27 2026 packager <lcensies@github.com> - 0.1.0-1
- Initial package
