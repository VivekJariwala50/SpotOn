pipeline {
    agent any

    environment {
        DOCKER_IMAGE = "danaziz/smart-parking-app"
        EC2_HOST = "ubuntu@3.139.64.245"
        APP_URL = "http://3.139.64.245:8000"
    }

    stages {

        stage('Build & Push Image') {
            steps {
                sh '''
                docker buildx create --use || true
                docker buildx inspect --bootstrap

                docker buildx build \
                --platform linux/amd64 \
                --provenance=false \
                --no-cache \
                -t $DOCKER_IMAGE \
                --push .
                '''
            }
        }

        stage('Deploy to EC2') {
            steps {
                sh """
               ssh -o StrictHostKeyChecking=no ${EC2_HOST} '

                docker network create parking-net || true

                # Start DB only if not exists (persistent)
                docker start postgres-db || docker run -d --name postgres-db \
                --network parking-net \
                -e POSTGRES_USER=admin \
                -e POSTGRES_PASSWORD=admin \
                -e POSTGRES_DB=parking \
                -v postgres_data:/var/lib/postgresql/data \
                -p 5432:5432 postgres

                sleep 10

                # ✅ FIX (missing in your version)
                docker stop app || true
                docker rm app || true

                docker pull --platform linux/amd64 ${DOCKER_IMAGE}

                docker run -d -p 8000:8000 --name app \
                --network parking-net \
                -e DATABASE_URL=postgresql://admin:admin@postgres-db:5432/parking \
                ${DOCKER_IMAGE}

                '
                """
            }
        }

                stage('Verify Deployment') {
                steps {
                sh '''
                sleep 20
                curl -v http://3.139.64.245:8000
                echo "http://3.139.64.245:8000"
                '''
               }
            }
         }

      post {
        success {
            withCredentials([string(credentialsId: 'SLACK_WEBHOOK', variable: 'SLACK_WEBHOOK')]) {
                sh """
                    curl -X POST -H 'Content-type: application/json' \
                    --data '{"text":"✅ Jenkins Build #${BUILD_NUMBER}\\n🚗 Smart Parking deployed successfully\\n🔗 http://3.139.64.245:8000"}' \
                    $SLACK_WEBHOOK
                """
            }
        }

        failure {
            withCredentials([string(credentialsId: 'SLACK_WEBHOOK', variable: 'SLACK_WEBHOOK')]) {
                sh """
                    curl -X POST -H 'Content-type: application/json' \
                    --data '{"text":"❌ Jenkins Build #${BUILD_NUMBER}\\n🚨 Deployment FAILED"}' \
                    $SLACK_WEBHOOK
                """
            }
        }

        always {
            echo "========================================"
            echo "Application is live at: http://3.139.64.245:8000"
            echo "========================================"
        }
    }
}
