# ocfilo F1 — GitHub main koruması uygulama öncesi plan

> **Durum: TASLAK / UYGULANMADI.** Bu belge GitHub repo ayarlarını değiştirmez, PR birleştirmez ve Autopilot oluşturmaz.

## Kanıtlanmış mevcut durum (13 Ekim 2026)

- Depo: `oaslananka-dev/oc-test`, varsayılan dal: `main`, `protected: false`, etkin ruleset: **0**.
- `fixture-quality` kontrolü yalnızca `playground/**` değişikliklerinde tetikleniyor; bu nedenle **tüm PR'larda zorunlu kontrol yapılmamalı**.
- Yeni `.github/workflows/ocfilo-baseline.yml` PR dosya filtresi **kullanmıyor**. `baseline` job'u `github-actions` uygulaması (`app_id: 15368`) tarafından bildirilir. Sadece git diff whitespace, değişen Python dosyalarının AST sözdizimi ve Bash dosyalarının `bash -n` kontrolünü yapar; tam güvenlik/işlev testi değildir.
- [PR #3](https://github.com/oaslananka-dev/oc-test/pull/3) üzerinde yalnızca `docs/` dosyası değişerek `pull_request` tetikleyicisiyle `baseline` kontrolü **başarıyla çalıştı** ([run 38338272079](https://github.com/oaslananka-dev/oc-test/actions/runs/38338272079)). Geçici PR kapatıldı, branch silindi, merge yapılmadı.
- PR #1 ve #2'nin mevcut HEAD commit'lerinde henüz `baseline` kontrolü yok. Ön koşullar yerine gelmeden zorunlu hâle getirilirse mevcut PR'lar beklemeye düşebilir.
- Depoda mevcut erişilebilir collaborator listesinde bir kullanıcı ve bağımsız ikinci reviewer **yok**. 1+ approval zorunluluğunu şimdi açmak incelemeyi fiilen kilitleyebilir.
- Repo genelinde `allow_auto_merge: true` ayarı var; bu özelliğin izinli olması etkin bir otomatik merge planı olduğu anlamına gelmez. **Faz 1 otomatik merge yapmaz.** Özelliğin repo genelinde kapatılması ayrıca kapsam kararı gerektirir.

## Önerilen ama henüz uygulanmayan koruma

1. `main` dalını koru; force-push ve branch silmeyi yasakla, yönetici bypass'ını en aza indir.
2. Birleştirmeden önce PR kullanımı gereksinimi koy. Tek kollaboratör koşulunda review onay sayısını başlangıçta **0** tut; bağımsız reviewer davet edildikten sonra **1+ onay** zorunluluğunu ayrıca değerlendir.
3. Zorunlu check olarak **`baseline`**, beklenen GitHub App olarak **`github-actions` (id 15368)** belirle. `strict` (base ile güncel) davranışının etkisini açıkça test et.
4. `fixture-quality` kontrolünü **zorunlu yapma**: `playground/**` ile sınırlı. Semgrep'i CI arızaları ve tüm PR'larda süreklilik kanıtlanana kadar ayrı/advisory izle.
5. İmza/linear history, conversation resolution, stale approval ve admin enforcement gibi ek seçenekleri gerçek repository kullanımına göre ayrıca değerlendir; otomatik merge tetikleme **yok**.

## Etkinleştirmeden önce kabul kapısı

- PR #1 ve #2 için yeni genel `baseline` kontrolünü gerçek `pull_request` olayı üzerinden çalıştır; aynı güncel SHA üzerinde başarıyla tamamlandığını teyit et.
- Zorunlu kontrolün tam adını ve yayıncı GitHub App kimliğini doğrula. Başarısız ve eksik check durumları engelleyici olmalı.
- Branch protection/ruleset kurallarının kendi ayarlarına GitHub API ile read-back yap; uygulanamayan/izin verilmeyen kontrol varsa varsayma.
- Review zorunluluğunu açmadan önce gerçekten bağımsız reviewer bulunduğunu doğrula.
- Geri alma planı ve test PR'larını etkileyecek değişiklikler gözden geçirilmeden repo ayarlarında değişiklik yapma.

## Faz 1'in değişmez sınırları

Yerel Multica/Antigravity yönetici, GitHub-hosted OpenCode Big Pickle worker; worker'da yazma yetkisi yok, ayrı güvenli yayınlayıcı kullanılır. Auto-merge ve production deploy yapılmaz. Önceki Multica Autopilot güvenlik engeli aşılmaz. `READY_FOR_MERGE` yalnızca F1 teknik değerlendirmesidir; GitHub enforced-policy doğrulaması ayrıca raporlanır.
