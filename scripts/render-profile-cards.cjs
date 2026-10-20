/**
 * Rebuild the profile visuals with Node.js and sharp.
 * Install sharp, then: node scripts/render-profile-cards.cjs
 * Exact values and provenance: assets/profile-project-sources.json.
 */
const fs=require('node:fs');
const path=require('node:path');
const sharp=require('sharp');
const root=path.resolve(__dirname,'..');
const sources=JSON.parse(fs.readFileSync(path.join(root,'assets/profile-project-sources.json'),'utf8'));
const xml=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const text=(x,y,size,value,extra='')=>`<text x="${x}" y="${y}" font-size="${size}" ${extra}>${xml(value)}</text>`;
function shell(height,body){return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="${height}" viewBox="0 0 1200 ${height}" role="img"><defs><linearGradient id="accent"><stop stop-color="#22d3ee"/><stop offset="1" stop-color="#a78bfa"/></linearGradient><linearGradient id="bar"><stop stop-color="#22bcd6"/><stop offset="1" stop-color="#818cf8"/></linearGradient></defs><rect x="1" y="1" width="1198" height="${height-2}" rx="26" fill="#080e20" stroke="#293752" stroke-width="2"/><g font-family="DejaVu Sans,Arial,sans-serif" fill="#eef2ff">${body}</g></svg>`;}
function header(eyebrow,title,subtitle){return text(52,52,20,eyebrow,'fill="#5cdef0" font-weight="700" letter-spacing="2"')+text(52,110,43,title,'font-weight="700"')+text(52,153,25,subtitle,'fill="#a5b4ce"')+'<rect x="52" y="181" width="1096" height="3" rx="1.5" fill="url(#accent)"/>';}
function nyc(){
  let body=header('01 / DATA SCIENCE','NYC School Insights','School surveys, SAT scores and housing context');
  body+=text(52,241,26,'Student-reported safety by borough','font-weight="600"');
  const left=264,width=822,top=274,bottom=602;
  for(let tick=0;tick<=8;tick+=2){const x=left+tick/8*width;body+=`<line x1="${x}" y1="${top}" x2="${x}" y2="${bottom}" stroke="#24314a" stroke-width="1"/>`+text(x,637,22,tick,'fill="#8e9fbd" text-anchor="middle"');}
  sources.nyc.values.forEach(({borough,score},i)=>{const y=306+i*62,w=score/8*width;body+=text(52,y+10,28,borough,'fill="#c6d1e6"')+`<rect x="${left}" y="${y-21}" width="${w}" height="34" rx="6" fill="${borough==='Manhattan'?'#a78bfa':'url(#bar)'}"/>`+text(left+w+14,y+8,25,score.toFixed(2),'fill="#eef2ff" font-weight="600"');});
  body+=text(676,680,23,'Mean safety score','fill="#a5b4ce" text-anchor="middle"')+text(52,731,20,'Source: saved notebook output / borough averages','fill="#8295b6"');
  return shell(760,body);
}
function adana(){
  const png=fs.readFileSync(path.join(root,sources.adana.localFigure)).toString('base64');
  let body=header('02 / TIME SERIES','Adana Energy & Climate','Fossil and renewable electricity generation, 2018–2022');
  body+='<rect x="28" y="209" width="1144" height="497" rx="12" fill="#060711"/>';
  body+=`<image x="30" y="210" width="1140" height="493.34" preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,${png}"/>`;
  body+=text(52,731,20,'Source: existing portfolio figure / monthly generation','fill="#8295b6"');return shell(760,body);
}
function ai(){
  let body=text(48,49,20,'03 / APPLIED AI','fill="#b7a0ff" font-weight="700" letter-spacing="2"')+text(48,101,38,'ZealCat AI Portfolio','font-weight="700"')+text(48,142,25,'A bilingual companion connecting portfolio context with Gemini.','fill="#a5b4ce"');
  const nodes=[{x:48,title:'Chat interface',detail:'Visitor messages · TR / EN'},{x:448,title:'Serverless API',detail:'Portfolio context + history'},{x:848,title:'Gemini',detail:'Generated responses'}];
  nodes.forEach((n,i)=>{body+=`<rect x="${n.x}" y="181" width="304" height="106" rx="15" fill="#0c1830" stroke="${i===1?'#8773dd':'#254961'}" stroke-width="2"/>`+text(n.x+22,222,26,n.title,'font-weight="600"')+text(n.x+22,259,19,n.detail,'fill="#9cacca"');if(i<2)body+=`<path d="M${n.x+326} 234 h48 m-10-8 10 8-10 8" fill="none" stroke="#62c4e8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`;});return shell(328,body);
}
function badges(){
  const source=JSON.parse(fs.readFileSync(path.join(root,'assets/profile-badges.json'),'utf8'));
  const directory=path.join(root,'assets/badges');fs.mkdirSync(directory,{recursive:true});
  for(const b of source.badges){const width=64+b.label.length*9,color=b.color==='violet'?'#a78bfa':'#22d3ee';const icon=b.stroke?`fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"`:`fill="${color}"`;
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="36" role="img" aria-label="${xml(b.label)}"><title>${xml(b.label)}</title><rect x="0.5" y="0.5" width="${width-1}" height="35" rx="7" fill="#0b1428" stroke="#2b3a56"/><g transform="translate(11 9) scale(.75)"><path d="${xml(b.path)}" ${icon}/></g><text x="39" y="23" font-family="DejaVu Sans,Arial,sans-serif" font-size="12" font-weight="600" letter-spacing=".5" fill="#e8eeff">${xml(b.label)}</text></svg>`;fs.writeFileSync(path.join(directory,b.id+'.svg'),svg+'\n');
  }
  console.log('Rendered local badges');
}
(async()=>{badges();for(const [name,svg]of[['project-nyc',nyc()],['project-adana',adana()],['project-zealcat',ai()]]){await sharp(Buffer.from(svg)).png({compressionLevel:9}).toFile(path.join(root,`assets/${name}.png`));console.log('Rendered '+name);}})().catch(e=>{console.error(e);process.exitCode=1;});
