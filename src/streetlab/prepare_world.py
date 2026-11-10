"""One entry point: GLB -> open sky -> residual cleanup -> non-emissive dark scene."""
import argparse
from datetime import datetime
from pathlib import Path

from streetlab import sky_cleanup
from streetlab import sky_cleanup_polish
from streetlab.cleanup_convergence import Progress,load_policy,geometry,read,save,REASONS,legacy_reviews


def main(argv=None):
    parser=argparse.ArgumentParser(description='GLBの蓋を除き、照明をゼロから置ける状態にします。')
    parser.add_argument('input',nargs='?',type=Path,help='元の見た目付きGLB')
    parser.add_argument('--cleaned-dir',type=Path,help='蓋の除去済みフォルダーを使う（APIなし）')
    parser.add_argument('--source-up',choices=('marble','gltf'),default='marble')
    parser.add_argument('--model',default=sky_cleanup.DEFAULT_MODEL)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--continue-dir',type=Path,help='最初の空除去処理を再開')
    args=parser.parse_args(argv)
    if args.cleaned_dir:
        if args.input or args.continue_dir:parser.error('--cleaned-dirは単独で使ってください。')
        output=args.cleaned_dir.resolve()
        if not (output/'sky_review.blend').is_file():parser.error('除去済みのsky_review.blendがありません。')
        sky_cleanup_polish.save_clean_scene(output)
    else:
        if not args.input:parser.error('GLBを指定してください。')
        policy=load_policy()
        print(f'清掃の終了条件: 追加修復なし／小さい変化が{policy["stable_rounds"]}回連続／形状不変／確認上限{policy["max_reviews"]}回',flush=True)
        if args.continue_dir:
            progress_file=args.continue_dir/'cleanup_progress.json'
            count=read(progress_file)['reviews_completed'] if progress_file.exists() else legacy_reviews(args.input)
            if count>=policy['max_reviews']:
                print(f'既に{count}回確認済みです。新しいHPC確認は行わず、最後の保護を反映して保存します。',flush=True)
        options=[str(args.input),'--quality','thorough','--source-up',args.source_up,'--model',args.model]
        if args.env_file:options+=['--env-file',str(args.env_file)]
        if args.continue_dir:options+=['--continue-dir',str(args.continue_dir)]
        cleaned=sky_cleanup.main(options)
        from streetlab.sky_cleanup_review import review
        state=cleaned/'preparation.json'
        progress=Progress(cleaned,args.input,policy)

        def complete(output,reason,mode):
            digest=geometry(cleaned)['sha256']
            last=progress.data['history'][-1] if progress.data['history'] else {}
            save(state,{'polished':str(output),'cleanup_sha256':digest,'status':'complete',
                'output_mode':mode,'termination_reason':reason,'reviews_completed':progress.count,
                'policy':progress.policy,'last_review_added_regions':last.get('added_regions'),
                'final_review_added_regions':0 if reason=='no_new_protection' else None,
                'final_review_skipped':reason!='no_new_protection'})
            progress.finish(reason)
            print('清掃終了:',REASONS[reason],f'（確認 {progress.count}回）',flush=True)
            return output

        def finish_base(reason):
            # Publish the rebuilt, protected base; do not perform another
            # destructive polish pass after the feedback budget is exhausted.
            print('現在の保護を反映した形を残し、清掃済みファイルを保存します。',flush=True)
            sky_cleanup_polish.save_clean_scene(cleaned)
            return complete(cleaned,reason,'bounded_base')

        def inspect(phase,after):
            print(f'清掃確認 {progress.count+1}/{progress.policy["max_reviews"]}: {phase}',flush=True)
            added=review(cleaned,after_folder=after,env_file=args.env_file,model=args.model)
            progress.record_review(phase,added,after)
            return added

        def rebuild():
            before=geometry(cleaned)
            options=[str(args.input),'--continue-dir',str(cleaned),'--model',args.model]
            if args.env_file:options+=['--env-file',str(args.env_file)]
            sky_cleanup.main(options)
            return progress.record_rebuild(before,geometry(cleaned))

        if progress.data.get('legacy_rounds'):
            print('以前の処理から引き継いだ確認回数:',progress.data['legacy_rounds'],flush=True)
        while True:
            if progress.exhausted:
                output=finish_base('max_reviews');break
            added=inspect('蓋除去後',cleaned)
            if not added:
                if progress.exhausted:
                    output=finish_base('max_reviews');break
                digest=geometry(cleaned)['sha256']
                saved=read(state) if state.exists() else {}
                if saved.get('cleanup_sha256')==digest and Path(saved['polished'])!=cleaned:
                    polished=Path(saved['polished'])
                else:
                    polished=cleaned.parent/(datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_polished')
                    save(state,{'polished':str(polished),'cleanup_sha256':digest})
                options=[str(cleaned),'--continue-dir',str(polished),'--model',args.model]
                if args.env_file:options+=['--env-file',str(args.env_file)]
                output=sky_cleanup_polish.main(options)
                added=inspect('仕上げ後',output)
                if not added:
                    output=complete(output,'no_new_protection','polished');break
            # Always apply the last accepted repairs before a bounded exit.
            reason=rebuild()
            if reason or progress.exhausted:
                output=finish_base(reason or 'max_reviews');break
    print('完了。照明を置くファイル: '+str(output/'street_relight.blend'),flush=True)
    print('GLB: '+str(output/'street_relight.glb'),flush=True)
    return output


if __name__=='__main__':main()
