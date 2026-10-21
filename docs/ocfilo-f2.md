# ocfilo Faz 2 — Çok Ajanlı Orkestrasyon: F2.1 Planlama Temeli

> **Editoryal yeniden yazım (21 Ekim 2026).** Bu belgenin depodaki ilk
> sürümünde 169 adet bozuk Unicode değiştirme karakteri (U+FFFD) ve iki adet
> kontrol karakteri vardı. Kaybolan özgün karakterler kaynak geçmişinden
> geri getirilemedi. Aşağıdaki metin **orijinalin birebir restorasyonu
> değildir**; doğrulanabilir F2.1 kodu ve aşama kayıtlarına dayanan,
> insan incelemesi gerektiren okunabilir bir yeniden anlatımdır.

## F2.1'in tarihsel kapsamı: güvenli, salt-okunur planlama

F2.1, en fazla 10 görevi ve bunların bağımlılıklarını doğrulayan deterministik
bir **plan-only** bileşenidir. Bir planın max_parallel=10 olması, 10 ajanın
başlatıldığına, çalıştığına veya güvenilir kapasite olarak kabul edildiğine
kanıt değildir. F2.1 planlayıcısı model çağırmaz, ajan çalıştırmaz ve dosya
değişikliklerini yayınlamaz. F1 çalışma akışına veya Multica'nın güvenlik
sınırlarına yetki eklemez.

Kaynaklar:

- Örnek manifest: examples/ocfilo-f2-ten-task-plan.json (şema sürümü 1).
- Planlayıcı: .github/scripts/ocfilo_f2_plan.py.
- Çevrimdışı testler: .github/scripts/test_ocfilo_f2_plan.py.
- Salt-okunur CI ve isteğe bağlı insan tetiklemesi:
  .github/workflows/ocfilo-f2-plan-tests.yml.
- İncelenen örnekte 10 görev, altı bağımlılık dalgasına ayrılır:
  **1 / 4 / 2 / 1 / 1 / 1**.

Plan çıktısı görevlerin bağımlılık dalgalarını ve dosya sahipliğini özetler.
Görev prompt'ları plan özetine taşınmaz; dispatch_allowed=false ve
execution_authorized=false değerleri korunur.

## Manifest ve yetki sınırları

Görevlerde task_id, prompt, model, agent=build, profile (base veya
runner-check), identity=actions-readonly, allowed_paths (görev başına
playground/ altında 1–8 tam dosya yolu), isteğe bağlı depends_on ve
issue_number alanları bulunur.

- Yalnız oaslananka-dev/oc-test deposu ve run_mode=plan-only kabul edilir.
- Model F1'den yeniden kullanılan ücretsiz model izin listesinde olmalıdır.
- Tanımlanmamış manifest alanları, yetkisiz kimlik/profil/model ve uygun
  olmayan istemler reddedilir. Manifestten keyfî MCP veya eklenti yetkisi
  sağlanmaz.
- Dosya sahipliği çakışması, izin verilmeyen dosya yolları, dizin geçişi
  (..), tekrarlanan yollar ve eksik/döngüsel bağımlılıklar reddedilir.
- issue_number, GitHub issue oluşturma izni değil, yalnız referans bilgisidir.
- Plan çıktısı var olan hedef dosyayı üzerine yazmaz.

## Yerel, modelsiz doğrulama

Aşağıdaki komutlar F2.1'in kendi plan-only bileşenini test eder; gerçek
OpenCode worker çalıştırmaz:

~~~bash
python3 .github/scripts/test_ocfilo_f2_plan.py -v
python3 .github/scripts/ocfilo_f2_plan.py \
  --input examples/ocfilo-f2-ten-task-plan.json \
  --output /tmp/ocfilo-f2-summary.json
~~~

Girdi manifestine gizli anahtar veya erişim belirteci konulmamalıdır.
İş akışı .github/workflows/ocfilo-f2-plan-tests.yml bu aşama için
salt-okunur plan testi ve önizlemesi üretir.

## Kademeli F2 yol haritası ve sonraki gelişmeler

1. **F2.1 — Plan-only:** Güvenli şema, DAG dalgaları, kesin dosya
   sahipliği, negatif testler ve salt-okunur CI. Tarihsel başlangıç kapsamı.
2. **F2.2 — Sınırlı çalıştırma pilotu:** İnsan tarafından başlatılmış
   1–3 sandbox worker; görev/issue/kaynak SHA/GitHub Actions çalıştırma
   kimliği bağları. Kanıtlar: [F2 worker pilotu](ocfilo-f2-pilot.md).
3. **F2.3 — Kontrollü kapasite ve kurtarma:** 3 → 5 → 10 eşzamanlılık
   ölçümü; deadline, retry, idempotency, iptal ve çakışma kontrolleri.
   **5-ajan operasyonel kapasite doğrulanmıştır.** Tek bir 10/10 temiz
   koşu, uzun dönem 10-ajan güvenilirliğini kanıtlamaz.
   Kanıtlar: [F2 kapasite raporu](ocfilo-f2-capacity.md).
4. **F2.4 — Kontrollü entegrasyon:** Tamamlanmış görev artefaktlarını
   izinli yollar ve kaynak SHA'ya göre doğrulayıp insanın inceleyeceği
   değişiklik önerisi hazırlama. Otomatik merge veya production deploy
   yetkisi değildir. İnceleme amaçlı PR #110 ve #118 ayrıca takip edilir.
5. **F2.5 — İsteğe bağlı Multica adaptörü:** Yalnız güvenilir GitHub
   olaylarıyla izlenebilir koordinasyon; engellenmiş yerel/native
   Autopilot yetkisi **Issue #6** çözülmeden aşılmaz.

**Güncel kabul durumu:** [F2 yol haritası Issue #10](https://github.com/oaslananka-dev/oc-test/issues/10).
Bu belgedeki ilk plan-only aşaması daha sonra geliştirilmiş runner, scheduler
ve entegrasyon araçlarıyla karıştırılmamalıdır. Özellikle F2.1 çıktısı
tek başına gerçek bir çalıştırma motoru, garanti edilmiş 10-ajan kapasitesi
veya gözetimsiz bir orkestrasyon yetkisi değildir.
