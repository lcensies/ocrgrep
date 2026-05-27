Name:           ocrgrep
Version:        0.1.0
Release:        1%{?dist}
Summary:        Parallel OCR grep over images
License:        MIT
URL:            https://github.com/lcensies/ocrgrep
Source0:        %{name}-%{version}.tar.gz

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
pip3 install --no-deps --no-index --find-links dist --root %{buildroot} --prefix /usr ocrgrep

%files
%license LICENSE
/usr/bin/ocrgrep
%{python3_sitelib}/ocr_grep*
%{python3_sitelib}/ocrgrep*

%changelog
* $(date '+%a %b %d %Y') packager <lcensies@gmail.com> - 0.1.0-1
- Initial package
