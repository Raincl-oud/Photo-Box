# Desain Aplikasi PhotoBox v0.4.0 ("Optical Instrument" Edition + Floating Console Deck + 13 Fitur Baru)

**Tanggal:** 2026-11-16  
**Versi Target:** `0.4.0`

---

## 1. Dunia Visual & Tata Letak Baru ("Optical Instrument" + "Floating Console Deck")

### A. Dunia Visual: *Optical Instrument* (Ala Leica SL / Hasselblad / Teenage Engineering)
- **Mode Black (*Matte Carbon & Anodized Graphite*):**
  - Latar Kanvas Utama: `#08080A` (*Velvet Optical Black*).
  - Modul Floating Console (Header HUD, Filmstrip Tray, Keycap Deck, Sidebar): `#121316` (*Anodized Graphite*) dengan border luar `1px #24262E` dan *top specular hairline* `1px rgba(255, 255, 255, 0.08)`.
  - Aksen Utama Instrumen: **Optical Amber (`#F59E0B`)** & **Crisp Optical White (`#F4F4F6`)** untuk indikator sesi aktif, bingkai *frame* aktif pada *filmstrip*, kapsul *viewfinder metadata*, serta tombol aksi utama (menggantikan warna biru template standar).
- **Mode White (*Machined Silver Aluminum & Studio Daylight*):**
  - Latar Kanvas Utama: `#ECEAE5` (*Warm Daylight Stone*).
  - Modul Floating Console: `#FAF9F6` dengan border `#D5D3CC` dan aksen `#D97706` / `#18181B`.

### B. Arsitektur Tata Letak: *Floating Console Deck*
- **Floating Top HUD Bar:** Melayang rapi di bagian atas (`border-radius: 10px`, margin `10px 14px 0 14px`) memuat:
  - Kiri: Toggle Sidebar, Nama Sesi, Info File, dan **Viewfinder Metadata Capsule** (*hover* = kartu layer metadata ke bawah; *klik* = popup *Full Metadata* di tengah).
  - Kanan: `CounterPill` (`1 / 150` interaktif *click-to-jump*), `Sort by : [Dropdown]`, tombol `Undo (Ctrl+Z)`, dan tombol `Settings` di pojok kanan atas.
- **Compact Center Filmstrip Tray:** Tidak lagi berupa bar panjang dari ujung kiri ke ujung kanan, melainkan **tray kapsul kompak di tengah bawah foto** yang hanya selebar 5 slot *thumbnail* (`#01`, `#02`, dst.).
- **Floating Keycap Deck & Fisik Tombol Keycap 3D Berbibir:**
  - Panel bawah melayang (`border-radius: 12px`, margin `0 14px 12px 14px`).
  - Setiap tombol kategori (`CategoryKeycapButton` & `TrashKeycapButton`) digambar secara presisi dengan **bibir bawah 3D (`2px` *bottom lip*)**, *top specular highlight*, kotak angka keycap `[1]` yang diwarnai aksen kategori (tanpa bulatan titik terpisah), **lampu indikator LED** jika foto yang sedang tampil berada di kategori tersebut, serta efek turun `1px` saat ditekan.

---

## 2. Spesifikasi 13 Pembaruan Fungsional v0.4.0

1. **`Prev (←)` & `Next (→)` Navigasi Murni Tanpa Meng-Undo Kategori + Ganti Kategori Langsung:**
   - `Prev (←)` dan `Next (→)` hanya menggeser foto yang dilihat tanpa membatalkan kategori foto yang sudah disortir.
   - Menekan tombol kategori lain pada foto yang sudah punya kategori akan langsung memindahkan file ke kategori baru dan maju 1 langkah (`i + 1`).
   - `Ctrl+Z` tetap khusus untuk **Undo** aksi pemindahan terakhir.
2. **Metadata Lookup di Header (Inline Viewfinder Capsule + Hover Layer + Click Full Popup + Pilih Field di Settings).**
3. **Saat Video Habis (`Loops.Once`), `Panah Kanan (→)` Langsung Pindah ke Next.**
4. **`Space + Kanan` Tanpa Perlu Mengangkat `Space` untuk Skip Video Beruntun.**
5. **`[Del] _Buang / Trash` di Posisi Paling Kiri & Masuk ke Default Categories di Settings.**
6. **Settings Per Menu (Sidebar Navigasi Kiri 7 Menu, Hanya Tampilkan 1 Isi Menu yang Dipilih).**
7. **In-Window Popup Overlay (Tidak Membuka Jendela Program OS Baru).**
8. **Kontrol Gambar Custom Background Disembunyikan Sampai Ada Gambar Custom.**
9. **Pengaturan Detik Seek Video (`1–60` detik) di Settings.**
10. **Pengaturan Perilaku Tombol Kanan/Kiri pada Video di Settings** (*Seek Video Duration* vs *Skip to Next/Prev Media*).
11. **Named Custom Keybind Profiles (Tombol `Custom` membuka editor Slot 1–9 + Input Nama + Save + Delete).**
12. **Perbaikan Kotak Putus-Putus (`border: dashed`) dengan Custom `QPainter` Rounded Rect & Escape `&&`.**
13. **Pengaturan Bahasa — Default Bahasa Inggris (`English`) + Opsi `English / Bahasa Indonesia` di Settings.**
