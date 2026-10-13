# ocfilo Faz 2 - Universal Multi-Agent Orchestration (ba�lang��)

## �lk teslimat: g�venli planlama

Faz 1 yerinde kal�r. F1 worker, PR observer, queue, policy ve mevcut
Multica g�venlik s�n�r� de�i�mez. Bu s�r�m **10 OpenCode ajan�n� ba�latt���n�
iddia etmez**: yaln�zca en fazla 10 g�rev i�in ba��ml�l�klar� ve dosya
sahipli�ini do�rulayan deterministik bir plan �retir.

- �rnek girdi: examples/ocfilo-f2-ten-task-plan.json (�ema s�r�m� 1).
- Planlay�c�: .github/scripts/ocfilo_f2_plan.py (a� ve subprocess yok).
- Testler: .github/scripts/test_ocfilo_f2_plan.py.
- CI ve iste�e ba�l� insan tetiklemesi: .github/workflows/ocfilo-f2-plan-tests.yml.
- �rnek: 10 g�rev, 6 ba��ml�l�k dalgas�: 1 / 4 / 2 / 1 / 1 / 1.
- ��kt� yaln�zca plan �zeti; prompt'lar artefakta yaz�lmaz.
  dispatch_allowed=false ve execution_authorized=false de�i�mez.

## G�rev profili ve yetki s�n�rlar�

Her g�revde task_id, prompt, model, agent=build,
profile=base veya runner-check, identity=actions-readonly,
allowed_paths (1-8 adet playground/ alt�nda tam dosya),
iste�e ba�l� depends_on ve issue_number bulunur.

- repository yaln�zca oaslananka-dev/oc-test; run_mode=plan-only.
- Model yaln�zca mevcut F1 �cretsiz model izin listesinde olmal�d�r.
- Yetkisiz MCP, plugin, PAT, ki�isel kimlik, publish alan� ve
  ek manifest alanlar� reddedilir.
- Dosya �ak��mas�, traversal, gizli/�zel dosya, farkl� harf b�y�kl���yle
  ayn� dosya, d�ng�sel ya da eksik ba��ml�l�klar fail-closed reddedilir.
- max_parallel=10 **10 worker �al��t�rmaz**, sadece 10'a kadar ba��ms�z
  g�revin ayn� dalgada yer almas�na izin verir. F1 MAX_WORKERS=3 de�i�medi.
- issue_number yaln�zca referans metadata's�d�r; otomatik issue a��lmaz.

## Yerel test

1. python3 .github/scripts/test_ocfilo_f2_plan.py -v
2. python3 .github/scripts/ocfilo_f2_plan.py --input examples/ocfilo-f2-ten-task-plan.json --output /tmp/ocfilo-f2-summary.json

Plan ��kt�s� mevcut dosyay� ezmez. GitHub Actions workflow'u push
de�i�ikliklerinde ve manuel ba�latmada salt-okunur test/�nizleme yapar,
OpenCode kurulmaz veya �al��t�r�lmaz. Manifest i�ine s�r yaz�lmamal�d�r.

## Kademeli kabul kap�lar�

1. F2.1 (bu PR): g�venli �ema, DAG, 10 g�rev �nizleme, negatif testler ve CI.
2. F2.2: g�rev/issue/HEAD-SHA/run kimlik ba�lar� ve g�venli y�r�tme
   profili. �lk canl� deney 1-3 sandbox worker ile s�nan�r.
3. F2.3: 3  5  10 kontroll� paralellik; kapasite, maliyet,
   h�z s�n�rlar�, idempotency, lease ve �ak��ma kontrol�.
4. F2.4: sonu� birle�tirme, test, code review ve insan-onayl� PR;
   otomatik merge veya deploy yok.
5. F2.5: iste�e ba�l� Multica koordinasyonu, mevcut native Autopilot
   g�venlik engeline dokunmadan.

F2'nin plan-only ��kt�s� �al��an bir execution engine, scheduler veya
g�zetimsiz ajan orkestrasyonu olarak sunulamaz.


