import 'dart:convert';
import 'dart:io';
import 'package:archive/archive_io.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';

void main() => runApp(const MaterialApp(home: Reader()));

class Reader extends StatefulWidget {
  const Reader({super.key});
  @override
  State<Reader> createState() => _ReaderState();
}

class _ReaderState extends State<Reader> {
  final player = AudioPlayer();
  String title = 'AutoBook';
  List<dynamic> sections = [];
  String status = '';
  bool busy = false;
  Directory? current;

  Future<void> openBook() async {
    setState(() { busy = true; status = ''; });
    Directory? staging;
    try {
      final result = await FilePicker.platform.pickFiles();
      final path = result?.files.single.path;
      if (path == null) return;
      final input = InputFileStream(path);
      try {
        final archive = ZipDecoder().decodeStream(input);
        final entry = archive.findFile('manifest.json');
        if (entry == null || entry.size > 32 * 1024 * 1024) {
          throw const FormatException('AutoBookを選択してください');
        }
        final book = jsonDecode(utf8.decode(entry.content));
        if (book['format'] != 'autobook' || book['version'] != 1) {
          throw const FormatException('未対応の書籍形式');
        }
        final name = book['audio']['file'];
        if (!['audio/book.mp3','audio/book.ogg'].contains(name)) {
          throw const FormatException('未対応の音声形式');
        }
        final audio = archive.findFile(name);
        if (audio == null) throw const FormatException('音声が見つかりません');
        staging = await (await getTemporaryDirectory()).createTemp('autobook-');
        final file = File('${staging.path}/${name.split('/').last}');
        final output = OutputFileStream(file.path);
        try { audio.writeContent(output); } finally { await output.close(); }
        await player.setFilePath(file.path);
        final previous = current;
        current = staging;
        staging = null;
        setState(() { title = book['title']; sections = book['timeline']; });
        await previous?.delete(recursive: true);
      } finally { await input.close(); }
    } catch (error) {
      setState(() => status = '開けませんでした。 $error');
    } finally {
      await staging?.delete(recursive: true);
      setState(() => busy = false);
    }
  }

  @override
  void dispose() {
    final directory = current;
    player.dispose().then((_) async { await directory?.delete(recursive: true); });
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(title)),
    body: SafeArea(child: Column(children: [
      FilledButton(onPressed: busy ? null : openBook,
        child: Text(busy ? '読み込み中…' : 'AutoBookを開く')),
      if (status.isNotEmpty) Text(status),
      if (current != null) StreamBuilder<PlayerState>(
        stream: player.playerStateStream,
        builder: (_, state) => IconButton(
          tooltip: player.playing ? '一時停止' : '再生',
          icon: Icon(player.playing ? Icons.pause : Icons.play_arrow),
          onPressed: () async {
            if (player.processingState == ProcessingState.completed) {
              await player.seek(Duration.zero);
              await player.play();
            } else if (player.playing) { await player.pause(); }
            else { await player.play(); }
          })),
      Expanded(child: ListView.builder(itemCount: sections.length,
        itemBuilder: (_, index) {
          final section = sections[index];
          return ListTile(title: Text(section['text']),
            subtitle: Text('${section['page']}ページ'),
            onTap: () async {
              await player.seek(Duration(milliseconds: section['start_ms']));
              await player.play();
            });
        })),
    ])));
}
