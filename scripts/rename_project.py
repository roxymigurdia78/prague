"""Migrate current files, with a fresh backup for each run."""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import shutil
import sys


def digest(path):
    with path.open('rb') as stream:
        result=hashlib.sha256()
        for block in iter(lambda:stream.read(1024*1024), b''):
            result.update(block)
        return result.hexdigest()


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['blender','text','verify','rollback'])
    parser.add_argument('--old-root',required=True,type=Path)
    parser.add_argument('--new-root',required=True,type=Path)
    parser.add_argument('--run-dir',required=True,type=Path)
    args=parser.parse_args(argv)
    root=args.new_root.resolve()
    old=str(args.old_root.absolute())
    new=str(root)
    run=args.run_dir.resolve()
    if root==args.old_root.absolute() or not run.is_relative_to(root/'scratch'):
        raise ValueError('Migration run must be inside the destination scratch directory.')
    backup=run/'backup'
    run.mkdir(parents=True,exist_ok=True)

    def remap(value):
        for a,b in [(old.replace('\\','\\\\'),new.replace('\\','\\\\')),
                    (old.replace('\\','/'),new.replace('\\','/')),(old,new)]:
            value=value.replace(a,b)
        return value

    def save_backup(path):
        target=backup/path.relative_to(root)
        if target.exists():
            raise RuntimeError(f'Backup already exists; use a fresh run: {target}')
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,target)

    if args.mode=='rollback':
        for path in backup.rglob('*'):
            if path.is_file():
                target=root/path.relative_to(backup)
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(path,target)
        print('Current-run backups restored.')
        return

    if args.mode=='blender':
        import bpy
        jobs=[]
        for job in (root/'outputs').rglob('job.json'):
            data=json.loads(job.read_text(encoding='utf-8-sig'))
            if isinstance(data,dict) and data.get('source') and data.get('sha256'):
                jobs.append({'file':str(job.relative_to(root)), 'source':data['source'], 'sha256':data['sha256']})
        (run/'jobs.json').write_text(json.dumps(jobs,indent=2),encoding='utf-8')

        def snapshot():
            meshes=[]
            for mesh in bpy.data.meshes:
                coords=array('f',[0])*(len(mesh.vertices)*3)
                mesh.vertices.foreach_get('co',coords)
                meshes.append((mesh.name,len(mesh.polygons),hashlib.sha256(coords.tobytes()).hexdigest()))
            return (sorted(meshes),
                    sorted((o.name,o.type,tuple(round(v,4) for row in o.matrix_world for v in row)) for o in bpy.data.objects),
                    sorted((m.name,len(m.node_tree.nodes) if m.node_tree else 0) for m in bpy.data.materials),
                    sorted((i.name,len(i.packed_files)) for i in bpy.data.images if i.type!='RENDER_RESULT'))

        def properties(owner):
            count=0
            for prop in owner.bl_rna.properties:
                if prop.type=='STRING' and not prop.is_readonly and prop.identifier not in {'name','name_full'}:
                    original=getattr(owner,prop.identifier)
                    updated=remap(original)
                    if original!=updated:
                        setattr(owner,prop.identifier,updated)
                        count+=1
            if isinstance(owner,bpy.types.ID):
                for key in list(owner.keys()):
                    original=owner[key]
                    if isinstance(original,str) and remap(original)!=original:
                        owner[key]=remap(original)
                        count+=1
            return count

        records=[]
        for path in sorted((root/'outputs').rglob('*.blend')):
            original_hash=digest(path)
            bpy.ops.wm.open_mainfile(filepath=str(path))
            bpy.context.preferences.filepaths.save_version=0
            before=snapshot()
            edits=0
            seen=set()
            for prop in bpy.data.bl_rna.properties:
                if prop.type!='COLLECTION':
                    continue
                for item in getattr(bpy.data,prop.identifier):
                    if item.as_pointer() in seen:
                        continue
                    seen.add(item.as_pointer())
                    edits+=properties(item)
                    tree=getattr(item,'node_tree',None)
                    if tree:
                        for node in tree.nodes:
                            edits+=properties(node)
            for scene in bpy.data.scenes:
                edits+=properties(scene.render)
            for text in bpy.data.texts:
                content=text.as_string()
                if remap(content)!=content:
                    text.from_string(remap(content))
                    edits+=1
            if edits:
                if digest(path)!=original_hash:
                    raise RuntimeError(f'File changed while migrating: {path}')
                save_backup(path)
                bpy.ops.wm.save_as_mainfile(filepath=str(path),check_existing=False,relative_remap=False,compress=True)
                bpy.ops.wm.open_mainfile(filepath=str(path))
                if snapshot()!=before:
                    raise RuntimeError(f'Scene verification failed: {path}')
            records.append({'file':str(path.relative_to(root)),'old_sha256':original_hash,'new_sha256':digest(path)})
            (run/'blends.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
        print(f'Checked {len(records)} current Blender files.')
        return

    records=json.loads((run/'blends.json').read_text(encoding='utf-8'))
    hashes={r['old_sha256']:r['new_sha256'] for r in records if r['old_sha256']!=r['new_sha256']}
    if args.mode=='text':
        for path in root.rglob('*'):
            relative=path.relative_to(root)
            if relative.parts[0] in {'.git','scratch','.venv','venv'} or '__pycache__' in relative.parts or not path.is_file():
                continue
            if path.name in {'rename_project.py','rename_project.ps1'}:
                continue
            if path.suffix.lower() not in {'.py','.md','.json','.cmd','.toml','.txt','.env'} and path.name!='.env':
                continue
            raw=path.read_bytes()
            try:
                text=raw.decode('utf-8')
            except UnicodeDecodeError:
                continue
            updated=remap(text)
            if relative.parts[0] in {'src','docs','config','tests','scripts'} or path.name=='README.md':
                updated=updated.replace(args.old_root.name,root.name)
            if path.suffix.lower()=='.json':
                for a,b in hashes.items():
                    updated=updated.replace(a,b)
                json.loads(updated.lstrip('\ufeff'))
            if text!=updated:
                save_backup(path)
                path.write_bytes(updated.encode('utf-8'))
        print('Updated current text references and source checksums.')
        return

    for record in records:
        if digest(root/record['file'])!=record['new_sha256']:
            raise RuntimeError(f'Blender file changed: {record["file"]}')
    for path in (root/'outputs').rglob('*.json'):
        text=path.read_text(encoding='utf-8-sig')
        if remap(text)!=text:
            raise RuntimeError(f'Old project reference: {path}')
        json.loads(text)
    # An older job may already refer to a source that the user later edited.
    # Preserve that history; only update hashes changed by this migration itself.
    for job in json.loads((run/'jobs.json').read_text(encoding='utf-8')):
        data=json.loads((root/job['file']).read_text(encoding='utf-8-sig'))
        if data['source']!=remap(job['source']) or data['sha256']!=hashes.get(job['sha256'],job['sha256']):
            raise RuntimeError(f'Job reference update mismatch: {job["file"]}')
    print('Current Blender files, paths, and source checksums verified.')


if __name__=='__main__':
    main(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
